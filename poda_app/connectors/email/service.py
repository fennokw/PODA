"""Unified mail service over Apple Mail and Gmail IMAP: accounts, permissions, reads, receipted actions."""
from __future__ import annotations

import time
import uuid
from typing import Any

from ...agent import receipts
from ...runtime.db import connect, now_iso, rows, one
from . import apple_mail, gmail_imap, state
from .models import MailError, decode_id, mask_address, address_hash

PERMISSIONS_NOTE = ("Read access only lists, reads, and searches. Modify adds mark/flag/archive/move/trash and drafts. "
                    "Sending stays off until enabled per account and each send still requires an explicit confirm.")


# ----------------------------------------------------------------------------- accounts

def _touch(account_id: str, error: str | None = None) -> None:
    conn = connect()
    try:
        state.ensure_tables(conn)
        if error:
            conn.execute("UPDATE email_accounts SET last_error=? WHERE id=?", (error[:300], account_id))
        else:
            conn.execute("UPDATE email_accounts SET last_success_at=?, last_error=NULL WHERE id=?", (now_iso(), account_id))
        conn.commit()
    finally:
        conn.close()


def get_account(account_id: str | None) -> dict[str, Any]:
    conn = connect()
    try:
        state.ensure_tables(conn)
        accounts = rows(conn, "SELECT * FROM email_accounts WHERE enabled=1 ORDER BY created_at ASC")
    finally:
        conn.close()
    if not accounts:
        raise MailError("not_found", "No mail account is connected.", "Connect an account in the Mail screen.", status=404)
    if not account_id:
        return accounts[0]
    acct = resolve_account_hint(account_id, accounts)
    if not acct:
        names = ", ".join(f"{a.get('account_name')} ({a.get('address_masked')}, id {a['id'][:8]}…)" for a in accounts)
        raise MailError("not_found", f"No connected mail account matches '{account_id}'. Connected: {names}.", "Use the account id, its name (e.g. Exchange, Google), or the address domain (e.g. school.example.test).", status=404)
    return acct


def resolve_account_hint(hint: str, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Accept an id, an account name, an address, or a domain/provider word ('school.example.test', 'gmail', 'school')."""
    h = (hint or "").strip().lower()
    if not h:
        return None
    for a in accounts:
        if a["id"] == hint or a["id"].startswith(h):
            return a
    for a in accounts:
        if (a.get("account_name") or "").lower() == h:
            return a
    domain = h.split("@")[-1] if "@" in h else h
    for a in accounts:
        masked = (a.get("address_masked") or "").lower()
        if domain and domain in masked:
            return a
        if h in {"gmail", "google", "personal"} and ("gmail" in masked or (a.get("account_name") or "").lower() in {"google", "gmail"}):
            return a
        if h in {"school", "exchange", "outlook", "work"} and ("school" in masked or (a.get("account_name") or "").lower() in {"school", "exchange", "outlook", "work"}):
            return a
    for a in accounts:
        if h in (a.get("account_name") or "").lower() or (a.get("account_name") or "").lower() in h:
            return a
    return None


def _sync_ledger() -> None:
    try:
        from ...runtime.capability import sync_ledger
        state.invalidate_cache()
        sync_ledger()
    except Exception:
        pass


def connect_apple(account_names: list[str] | None, allow_modify: bool = False, allow_send: bool = False, auto_launch: bool = True) -> dict[str, Any]:
    if not apple_mail.is_running():
        if not auto_launch:
            raise MailError("mail_not_running", "Mail is not running.", "Open Mail or connect with auto_launch=true.", status=409)
        apple_mail.launch()
        for _ in range(20):
            if apple_mail.is_running():
                break
            time.sleep(0.5)
    probe = apple_mail._run("status", {}, timeout=30)  # raises MailError(automation_denied) on TCC refusal
    detected = probe.get("accounts", [])
    wanted = {n.strip() for n in (account_names or []) if n and n.strip()}
    chosen = [a for a in detected if a.get("enabled") is not False and (not wanted or a.get("account_name") in wanted)]
    if not chosen:
        raise MailError("not_found", "No matching enabled Mail account was found.", "Check the account names in Mail → Settings → Accounts.", status=404)
    conn = connect()
    try:
        state.ensure_tables(conn)
        saved = []
        for a in chosen:
            addrs = a.get("addresses") or []
            addr = addrs[0] if addrs else ""
            existing = one(conn, "SELECT * FROM email_accounts WHERE provider='apple_mail' AND account_name=?", (a["account_name"],))
            if existing:
                conn.execute("UPDATE email_accounts SET enabled=1, allow_modify=?, allow_send=?, address_masked=?, address_hash=?, provider_type=?, last_error=NULL WHERE id=?",
                             (1 if allow_modify else 0, 1 if allow_send else 0, mask_address(addr), address_hash(addr), a.get("provider_type"), existing["id"]))
                aid = existing["id"]
            else:
                aid = str(uuid.uuid4())
                conn.execute("INSERT INTO email_accounts (id, provider, account_name, address_masked, address_hash, address, provider_type, enabled, allow_modify, allow_send, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (aid, "apple_mail", a["account_name"], mask_address(addr), address_hash(addr), None, a.get("provider_type"), 1, 1 if allow_modify else 0, 1 if allow_send else 0, now_iso()))
            saved.append(aid)
        conn.execute("UPDATE integration_state SET active=1, mode='apple_mail', detail=?, last_verified_at=?, last_success_at=? WHERE integration='email'",
                     (f"Apple Mail connected ({len(saved)} account(s)).", now_iso(), now_iso()))
        conn.commit()
        accounts = [state.public_account(r) for r in rows(conn, "SELECT * FROM email_accounts WHERE enabled=1 ORDER BY created_at")]
    finally:
        conn.close()
    _sync_ledger()
    return {"connected": True, "accounts": accounts}


def connect_gmail(address: str, app_password: str, allow_modify: bool = False, allow_send: bool = False) -> dict[str, Any]:
    address = (address or "").strip().lower()
    if "@" not in address or not app_password.strip():
        raise MailError("validation", "Address and app password are required.", status=400)
    check = gmail_imap.verify_login(address, app_password)  # raises invalid_credentials
    gmail_imap.store_password(address, app_password)
    conn = connect()
    try:
        state.ensure_tables(conn)
        existing = one(conn, "SELECT * FROM email_accounts WHERE provider='gmail_imap' AND address=?", (address,))
        if existing:
            conn.execute("UPDATE email_accounts SET enabled=1, allow_modify=?, allow_send=?, last_success_at=?, last_error=NULL WHERE id=?",
                         (1 if allow_modify else 0, 1 if allow_send else 0, now_iso(), existing["id"]))
            aid = existing["id"]
        else:
            aid = str(uuid.uuid4())
            conn.execute("INSERT INTO email_accounts (id, provider, account_name, address_masked, address_hash, address, provider_type, enabled, allow_modify, allow_send, created_at, last_success_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (aid, "gmail_imap", "Gmail", mask_address(address), address_hash(address), address, "imap", 1, 1 if allow_modify else 0, 1 if allow_send else 0, now_iso(), now_iso()))
        conn.execute("UPDATE integration_state SET active=1, mode='gmail_imap', detail=?, last_verified_at=?, last_success_at=? WHERE integration='email'",
                     (f"Gmail IMAP connected for {mask_address(address)} (inbox {check.get('inbox_count')} messages).", now_iso(), now_iso()))
        conn.commit()
        acct = one(conn, "SELECT * FROM email_accounts WHERE id=?", (aid,))
    finally:
        conn.close()
    _sync_ledger()
    return {"connected": True, "account": state.public_account(acct)}


def set_permissions(account_id: str, allow_modify: bool, allow_send: bool) -> dict[str, Any]:
    acct = get_account(account_id)
    conn = connect()
    try:
        conn.execute("UPDATE email_accounts SET allow_modify=?, allow_send=? WHERE id=?", (1 if allow_modify else 0, 1 if allow_send else 0, acct["id"]))
        conn.commit()
        acct = one(conn, "SELECT * FROM email_accounts WHERE id=?", (acct["id"],))
    finally:
        conn.close()
    _sync_ledger()
    return {"account": state.public_account(acct)}


def revoke(account_id: str) -> dict[str, Any]:
    acct = get_account(account_id)
    if acct["provider"] == "gmail_imap" and acct.get("address"):
        gmail_imap.delete_password(acct["address"])
    conn = connect()
    try:
        conn.execute("DELETE FROM email_accounts WHERE id=?", (acct["id"],))
        remaining = conn.execute("SELECT COUNT(*) FROM email_accounts WHERE enabled=1").fetchone()[0]
        if remaining == 0:
            conn.execute("UPDATE integration_state SET active=0, mode='on_demand_imap_preview', detail='No persistent email account is connected. Credentials are request-scoped and not stored.', account_identifier=NULL, last_verified_at=? WHERE integration='email'", (now_iso(),))
        conn.commit()
    finally:
        conn.close()
    _sync_ledger()
    return {"revoked": True, "remaining_accounts": remaining}


def status() -> dict[str, Any]:
    conn = connect()
    try:
        state.ensure_tables(conn)
        accounts = [state.public_account(r) for r in rows(conn, "SELECT * FROM email_accounts WHERE enabled=1 ORDER BY created_at")]
        gmail = one(conn, "SELECT * FROM email_accounts WHERE provider='gmail_imap' AND enabled=1 ORDER BY created_at LIMIT 1")
        integration = one(conn, "SELECT * FROM integration_state WHERE integration='email'") or {}
    finally:
        conn.close()
    state.invalidate_cache()
    apple = state.apple_status_cached(max_age=0)
    return {
        "capability": state.capability(integration),
        "providers": {
            "apple_mail": apple,
            "gmail_imap": {"configured": bool(gmail), "address_masked": gmail["address_masked"] if gmail else None,
                           "last_success_at": gmail["last_success_at"] if gmail else None, "error": gmail.get("last_error") if gmail else None},
        },
        "accounts": accounts,
        "permissions_note": PERMISSIONS_NOTE,
    }


# ----------------------------------------------------------------------------- reads

def _require_read(acct: dict[str, Any]) -> None:
    if acct["provider"] == "apple_mail":
        st = state.apple_status_cached()
        if st.get("authorized") is False:
            raise MailError("automation_denied", "Apple Mail automation is denied.", apple_mail.AUTOMATION_REMEDY, status=403)


def mailboxes(account_id: str | None) -> dict[str, Any]:
    acct = get_account(account_id)
    _require_read(acct)
    try:
        if acct["provider"] == "apple_mail":
            out = apple_mail.mailboxes(acct["account_name"])
        else:
            out = gmail_imap.mailboxes(acct["address"])
        _touch(acct["id"])
        return {"account_id": acct["id"], "mailboxes": out}
    except MailError as exc:
        _touch(acct["id"], exc.message)
        raise


def list_messages(account_id: str | None, mailbox: str = "INBOX", limit: int = 50, offset: int = 0, unread_only: bool = False,
                  query: str | None = None, snippet_limit: int = 0) -> dict[str, Any]:
    acct = get_account(account_id)
    _require_read(acct)
    started = time.perf_counter()
    try:
        if acct["provider"] == "apple_mail":
            out = apple_mail.list_messages(acct["account_name"], acct["id"], mailbox, limit, offset, unread_only, query, snippet_limit=snippet_limit)
        else:
            out = gmail_imap.list_messages(acct["address"], acct["id"], mailbox, limit, offset, unread_only, query)
        _touch(acct["id"])
    except MailError as exc:
        _touch(acct["id"], exc.message)
        raise
    out.update({"account_id": acct["id"], "offset": offset, "limit": limit, "elapsed_ms": int((time.perf_counter() - started) * 1000)})
    return out


def snippets(account_id: str | None, ids: list[str]) -> dict[str, str]:
    acct = get_account(account_id)
    _require_read(acct)
    out: dict[str, str] = {}
    if acct["provider"] == "apple_mail":
        by_mailbox: dict[str, list[tuple[str, str]]] = {}
        for mid in ids[:10]:
            d = decode_id(mid)
            by_mailbox.setdefault(d["mailbox"], []).append((mid, d["native_id"]))
        for mailbox, pairs in by_mailbox.items():
            got = apple_mail.snippets(acct["account_name"], mailbox, [n for _, n in pairs])
            for mid, native in pairs:
                out[mid] = got.get(str(native), "")
    else:
        for mid in ids[:10]:
            d = decode_id(mid)
            try:
                out[mid] = gmail_imap.read_message(acct["address"], acct["id"], d["mailbox"], d["native_id"])["snippet"]
            except MailError:
                out[mid] = ""
    return out


def read_message(account_id: str | None, message_id: str) -> dict[str, Any]:
    acct = get_account(account_id)
    _require_read(acct)
    d = decode_id(message_id)
    try:
        if acct["provider"] == "apple_mail":
            out = apple_mail.read_message(acct["account_name"], acct["id"], d["mailbox"], d["native_id"])
        else:
            out = gmail_imap.read_message(acct["address"], acct["id"], d["mailbox"], d["native_id"])
        _touch(acct["id"])
        return out
    except MailError as exc:
        _touch(acct["id"], exc.message)
        raise


def search(query: str, account_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    accounts = [get_account(account_id)] if account_id else [a for a in state.list_accounts()]
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for acct in accounts:
        try:
            _require_read(acct)
            if acct["provider"] == "apple_mail":
                names = [m["name"] for m in apple_mail.mailboxes(acct["account_name"])]
                targets = ["INBOX"]
                try:
                    targets.append(apple_mail.archive_target(acct.get("provider_type"), names))
                except MailError:
                    pass
                for mb in targets:
                    results.extend(apple_mail.list_messages(acct["account_name"], acct["id"], mb, limit=limit, query=query)["messages"])
            else:
                results.extend(gmail_imap.search(acct["address"], acct["id"], query, limit))
            _touch(acct["id"])
        except MailError as exc:
            errors.append(f"{acct['account_name']}: {exc.message}")
    seen, dedup = set(), []
    for r in results:
        key = r.get("message_id") or r["id"]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    dedup.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"query": query, "messages": dedup[:limit], "errors": errors}


# ----------------------------------------------------------------------------- receipted actions

ACTIONS = {"mark_read", "mark_unread", "flag", "unflag", "archive", "move", "trash"}


def _require_modify(acct: dict[str, Any]) -> None:
    if not acct.get("allow_modify"):
        raise MailError("not_allowed", f"Account {acct['account_name']} is connected read-only.", "Enable 'modify' for this account in the Mail screen.", status=403)


def act(account_id: str | None, message_id: str, action: str, mailbox: str | None = None, confirm: bool = False, session_id: str | None = None) -> dict[str, Any]:
    if action not in ACTIONS:
        raise MailError("validation", f"Unknown action {action}", status=400)
    acct = get_account(account_id)
    _require_read(acct)
    _require_modify(acct)
    if action == "trash" and not confirm:
        raise MailError("confirm_required", "Moving a message to trash requires confirm=true.", status=400)
    if action == "move" and not mailbox:
        raise MailError("validation", "move requires a target mailbox", status=400)
    d = decode_id(message_id)
    rid = receipts.open_receipt(f"mail_{action}", f"{acct['account_name']}:{d['mailbox']}:{d['native_id']}", {"action": action, "mailbox": mailbox, "account": acct["account_name"]},
                                approved=True, session_id=session_id)
    try:
        if acct["provider"] == "apple_mail":
            name = acct["account_name"]
            if action in {"mark_read", "mark_unread"}:
                st = apple_mail.set_flags(name, d["mailbox"], d["native_id"], read=(action == "mark_read"))
                ok = st.get("read") == (action == "mark_read")
            elif action in {"flag", "unflag"}:
                st = apple_mail.set_flags(name, d["mailbox"], d["native_id"], flagged=(action == "flag"))
                ok = st.get("flagged") == (action == "flag")
            elif action == "archive":
                target = apple_mail.archive_target(acct.get("provider_type"), [m["name"] for m in apple_mail.mailboxes(name)])
                st = apple_mail.move(name, d["mailbox"], d["native_id"], target)
                ok = bool(st.get("moved"))
                st = {"read": None, "flagged": None, "mailbox": target, **st}
            elif action == "move":
                st = apple_mail.move(name, d["mailbox"], d["native_id"], mailbox)
                ok = bool(st.get("moved"))
                st = {"read": None, "flagged": None, **st}
            else:
                st = apple_mail.trash(name, d["mailbox"], d["native_id"])
                ok = bool(st.get("trashed"))
                st = {"read": None, "flagged": None, "mailbox": "Trash", **st}
        else:
            addr = acct["address"]
            if action in {"mark_read", "mark_unread"}:
                st = gmail_imap.set_flags(addr, d["mailbox"], d["native_id"], read=(action == "mark_read"))
                ok = st.get("read") == (action == "mark_read")
            elif action in {"flag", "unflag"}:
                st = gmail_imap.set_flags(addr, d["mailbox"], d["native_id"], flagged=(action == "flag"))
                ok = st.get("flagged") == (action == "flag")
            elif action == "archive":
                st = {"read": None, "flagged": None, **gmail_imap.archive(addr, d["mailbox"], d["native_id"])}
                ok = bool(st.get("moved"))
            elif action == "move":
                st = {"read": None, "flagged": None, **gmail_imap.move(addr, d["mailbox"], d["native_id"], mailbox)}
                ok = bool(st.get("moved"))
            else:
                st = {"read": None, "flagged": None, "mailbox": "[Gmail]/Trash", **gmail_imap.trash(addr, d["mailbox"], d["native_id"])}
                ok = bool(st.get("trashed"))
        state_after = {"read": st.get("read"), "flagged": st.get("flagged"), "mailbox": st.get("mailbox") or d["mailbox"]}
        if not ok:
            receipt = receipts.close_receipt(rid, "failed", "re-read after action did not show the expected state", detail=str(state_after), error="state mismatch after action")
            _touch(acct["id"], "action verification failed")
            raise MailError("provider", f"Mail did not confirm the {action}; the receipt is marked failed.", status=502)
        receipt = receipts.close_receipt(rid, "succeeded", f"re-read after action: {state_after}", detail=f"{action} on {d['mailbox']}:{d['native_id']}")
        _touch(acct["id"])
        return {"receipt": receipt, "state_after": state_after}
    except MailError as exc:
        if exc.kind not in {"provider"} or "receipt is marked failed" not in exc.message:
            receipts.close_receipt(rid, "failed", "no state change verified", error=exc.message)
        _touch(acct["id"], exc.message)
        raise
    except Exception as exc:
        receipts.close_receipt(rid, "failed", "no state change verified", error=str(exc))
        _touch(acct["id"], str(exc))
        raise MailError("provider", f"Mail action failed: {exc}", status=502) from exc


def _quote_reply(acct: dict[str, Any], reply_to_message_id: str | None) -> tuple[list[str], str | None, str, str | None]:
    """Returns (to, subject_prefix, quoted_body, rfc_message_id) for a reply."""
    if not reply_to_message_id:
        return [], None, "", None
    original = read_message(acct["id"], reply_to_message_id)
    d = decode_id(reply_to_message_id)
    # The masked address cannot be replied to; fetch the raw sender through the provider.
    if acct["provider"] == "apple_mail":
        raw = apple_mail._run("read", {"account": acct["account_name"], "mailbox": d["mailbox"], "native_id": d["native_id"], "max_chars": 20000}, timeout=30)
        from .models import split_sender
        _, addr = split_sender(raw.get("reply_to") or raw.get("sender"))
    else:
        from .models import split_sender
        _, addr = split_sender(original["headers"].get("reply_to") or "")
        if not addr:
            # IMAP read exposes only masked sender in `original`; re-fetch raw header via provider
            addr = _gmail_raw_sender(acct["address"], d["mailbox"], d["native_id"])
    quoted = "\n".join("> " + line for line in (original.get("body_text") or "")[:4000].splitlines())
    subject = original.get("subject") or ""
    return ([addr] if addr else []), ("Re: " + subject if not subject.lower().startswith("re:") else subject), f"\n\nOn {original.get('date')}, {original.get('from_name')} wrote:\n{quoted}", original.get("message_id")


def _gmail_raw_sender(address: str, mailbox: str, uid: str) -> str:
    import email as _email
    with gmail_imap._Session(address) as s:
        s.select(mailbox)
        typ, data = s.conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM REPLY-TO)])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return ""
        hdr = _email.message_from_bytes(data[0][1])
        from .models import split_sender
        _, addr = split_sender(gmail_imap._hdr(hdr.get("Reply-To") or hdr.get("From")))
        return addr


def create_draft(account_id: str | None, to: list[str], subject: str, body: str, cc: list[str] | None = None, reply_to_message_id: str | None = None,
                 session_id: str | None = None) -> dict[str, Any]:
    acct = get_account(account_id)
    _require_read(acct)
    _require_modify(acct)
    rto, rsubject, quoted, rfc = _quote_reply(acct, reply_to_message_id)
    to = [a.strip() for a in (to or []) if a and a.strip()] or rto
    subject = subject or rsubject or ""
    if not to:
        raise MailError("validation", "At least one recipient is required.", status=400)
    rid = receipts.open_receipt("mail_draft", f"{acct['account_name']}:draft", {"to": [mask_address(a) for a in to], "subject": subject[:120], "reply": bool(reply_to_message_id)}, approved=True, session_id=session_id)
    try:
        if acct["provider"] == "apple_mail":
            out = apple_mail.draft(subject, body + quoted, to, cc or [], sender=None, account=acct["account_name"])
            draft_id = out.get("draft_id")
            verified = False
            try:
                time.sleep(0.8)  # Mail files the draft asynchronously
                found = apple_mail.find_draft(None, subject).get("found", [])
                verified = bool(found)
                if found:
                    draft_id = f"{found[0].get('account')}:{found[0]['mailbox']}:{found[0]['native_id']}"
            except MailError:
                pass
            if not out.get("saved") and not verified:
                raise MailError("provider", "Mail did not report the draft as saved.", status=502)
            verification = "draft found in Drafts mailbox after save" if verified else "Mail reported save; Drafts lookup inconclusive"
        else:
            out = gmail_imap.draft(acct["address"], to, subject, body + quoted, cc, rfc)
            draft_id = out["draft_id"]
            verification = "IMAP APPEND to [Gmail]/Drafts acknowledged"
        receipt = receipts.close_receipt(rid, "succeeded", verification, detail=f"draft {draft_id}")
        _touch(acct["id"])
        return {"receipt": receipt, "draft_id": draft_id}
    except MailError as exc:
        receipts.close_receipt(rid, "failed", "no draft verified", error=exc.message)
        _touch(acct["id"], exc.message)
        raise


def send(account_id: str | None, to: list[str], subject: str, body: str, cc: list[str] | None = None, reply_to_message_id: str | None = None,
         confirm: bool = False, session_id: str | None = None) -> dict[str, Any]:
    acct = get_account(account_id)
    _require_read(acct)
    if not acct.get("allow_send"):
        raise MailError("not_allowed", f"Sending is disabled for {acct['account_name']}.", "Enable 'send' for this account in the Mail screen (off by default).", status=403)
    if not confirm:
        raise MailError("confirm_required", "Sending requires confirm=true.", status=400)
    rto, rsubject, quoted, rfc = _quote_reply(acct, reply_to_message_id)
    to = [a.strip() for a in (to or []) if a and a.strip()] or rto
    subject = subject or rsubject or ""
    if not to:
        raise MailError("validation", "At least one recipient is required.", status=400)
    rid = receipts.open_receipt("mail_send", f"{acct['account_name']}:send", {"to": [mask_address(a) for a in to], "subject": subject[:120]}, approved=True, session_id=session_id)
    try:
        if acct["provider"] == "apple_mail":
            out = apple_mail.draft(subject, body + quoted, to, cc or [], sender=None, send=True, account=acct["account_name"])
            ok = bool(out.get("sent"))
            verification = "Mail accepted the send command" if ok else "Mail did not confirm send"
        else:
            out = gmail_imap.send(acct["address"], to, subject, body + quoted, cc, rfc)
            ok = bool(out.get("sent"))
            verification = f"SMTP accepted message {out.get('message_id')}" if ok else f"SMTP refused: {out.get('refused')}"
        if not ok:
            receipts.close_receipt(rid, "failed", verification, error="send not confirmed")
            raise MailError("provider", "The provider did not confirm the send; receipt marked failed.", status=502)
        receipt = receipts.close_receipt(rid, "succeeded", verification, detail=f"to {', '.join(mask_address(a) for a in to)}")
        _touch(acct["id"])
        return {"receipt": receipt}
    except MailError as exc:
        if "receipt marked failed" not in exc.message:
            receipts.close_receipt(rid, "failed", "no send verified", error=exc.message)
        _touch(acct["id"], exc.message)
        raise


def mail_receipts(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows(conn, "SELECT * FROM action_receipts WHERE tool LIKE 'mail_%' ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 500)),))
    finally:
        conn.close()
