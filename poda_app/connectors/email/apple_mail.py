"""Apple Mail bridge via `osascript -l JavaScript` (JXA).

All user-controlled values travel as a single JSON argument (never interpolated into the script).
Scripts fetch bulk property arrays (one Apple Event per property per mailbox) which measured 5-100 ms for
a 200-message mailbox on this Mac; message index 0 is the newest.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .models import MailError, encode_id, split_sender, snippet, mask_address

PROVIDER = "apple_mail"
LIST_TIMEOUT = 20
READ_TIMEOUT = 30
ACTION_TIMEOUT = 30
AUTOMATION_REMEDY = ("System Settings → Privacy & Security → Automation → allow Terminal (or the app that launched PODA) to control Mail. "
                     "Then retry; Mail must be allowed for PODA's process.")

_PRELUDE = r'''
function parseArgs(argv){ try { return JSON.parse(argv[0] || "{}"); } catch (e) { return {}; } }
function mailApp(){ return Application("Mail"); }
function findAccount(Mail, name){
  const accts = Mail.accounts();
  for (const a of accts) { if (a.name() === name) return a; }
  throw new Error("NOT_FOUND: account " + name);
}
function allMailboxes(acct){
  const out = [];
  const walk = (mbs, prefix, depth) => {
    if (depth > 4) return;
    for (const m of mbs) {
      let name = ""; try { name = m.name(); } catch (e) { continue; }
      const path = prefix ? prefix + "/" + name : name;
      out.push({name, path, ref: m});
      if (out.length >= 200) return;
      try { const kids = m.mailboxes(); if (kids && kids.length) walk(kids, path, depth + 1); } catch (e) {}
    }
  };
  walk(acct.mailboxes(), "", 0);
  return out;
}
function findMailbox(acct, wanted){
  const w = String(wanted || "INBOX");
  try { const direct = acct.mailboxes.byName(w); const nm = direct.name(); if (nm === w) return {name: nm, path: w, ref: direct}; } catch (e) {}
  const mbs = allMailboxes(acct);
  for (const m of mbs) { if (m.path === w) return m; }
  for (const m of mbs) { if (m.name === w) return m; }
  const lw = w.toLowerCase();
  for (const m of mbs) { if (m.name.toLowerCase() === lw || m.path.toLowerCase() === lw) return m; }
  throw new Error("NOT_FOUND: mailbox " + w);
}
function safe(fn, dflt){ try { return fn(); } catch (e) { return dflt; } }
function iso(d){ try { return new Date(d).toISOString(); } catch (e) { return null; } }
'''

_SCRIPTS: dict[str, str] = {
    "status": r'''
function run(argv){
  const Mail = mailApp();
  const running = Mail.running();
  const accounts = [];
  for (const a of Mail.accounts()) {
    accounts.push({account_name: a.name(), provider_type: safe(() => String(a.accountType()), "unknown"), enabled: safe(() => a.enabled(), null),
                   addresses: safe(() => a.emailAddresses(), [])});
  }
  return JSON.stringify({running, accounts});
}''',
    "mailboxes": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const out = [];
  for (const m of allMailboxes(acct)) {
    out.push({name: m.path, unread: safe(() => m.ref.unreadCount(), null), count: null});
  }
  return JSON.stringify({mailboxes: out});
}''',
    "list": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const mb = findMailbox(acct, args.mailbox).ref;
  const limit = Math.max(1, Math.min(args.limit || 50, 100)); const offset = Math.max(0, args.offset || 0);
  const msgs = mb.messages; const total = msgs.length;
  const window = Math.min(total, (args.unread_only || args.query) ? Math.min(total, 1500) : offset + limit);
  const rows = [];
  if (window > 0) {
    const ids = msgs.id(); const dates = msgs.dateReceived(); const subs = msgs.subject(); const senders = msgs.sender();
    const reads = msgs.readStatus(); const flags = msgs.flaggedStatus(); const mids = msgs.messageId();
    const q = (args.query || "").toLowerCase();
    let unread = 0;
    for (let i = 0; i < total; i++) { if (reads[i] === false) unread++; }
    let kept = 0, skipped = 0;
    for (let i = 0; i < window && rows.length < limit; i++) {
      if (args.unread_only && reads[i] !== false) continue;
      if (q && !(String(subs[i] || "").toLowerCase().includes(q) || String(senders[i] || "").toLowerCase().includes(q))) continue;
      if (skipped < offset) { skipped++; continue; }
      rows.push({native_id: ids[i], date: iso(dates[i]), subject: subs[i], sender: senders[i], read: reads[i], flagged: flags[i], message_id: mids[i]});
    }
    return JSON.stringify({total, unread, rows, scanned: window});
  }
  return JSON.stringify({total: 0, unread: 0, rows: [], scanned: 0});
}''',
    "read": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const mb = findMailbox(acct, args.mailbox).ref;
  const m = mb.messages.byId(Number(args.native_id));
  const subject = m.subject();
  const atts = [];
  try { for (const a of m.mailAttachments()) { atts.push({name: safe(() => a.name(), ""), size: safe(() => a.fileSize(), null), mime: safe(() => a.mimeType(), null)}); } } catch (e) {}
  const to = safe(() => m.toRecipients().map(r => safe(() => r.address(), "")), []);
  const cc = safe(() => m.ccRecipients().map(r => safe(() => r.address(), "")), []);
  let content = ""; try { content = m.content() || ""; } catch (e) { content = ""; }
  return JSON.stringify({native_id: m.id(), subject, sender: m.sender(), date: iso(m.dateReceived()), read: m.readStatus(), flagged: m.flaggedStatus(),
                         message_id: safe(() => m.messageId(), null), reply_to: safe(() => m.replyTo(), null), to, cc, attachments: atts, body: String(content).slice(0, args.max_chars || 200000)});
}''',
    "snippets": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const mb = findMailbox(acct, args.mailbox).ref; const out = {};
  for (const id of (args.ids || []).slice(0, 60)) { try { const m = mb.messages.byId(Number(id)); out[String(id)] = String(m.content() || "").slice(0, 400); } catch (e) { out[String(id)] = ""; } }
  return JSON.stringify(out);
}''',
    "set_flags": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const mb = findMailbox(acct, args.mailbox).ref; const m = mb.messages.byId(Number(args.native_id));
  if (args.read !== undefined && args.read !== null) m.readStatus = !!args.read;
  if (args.flagged !== undefined && args.flagged !== null) m.flaggedStatus = !!args.flagged;
  return JSON.stringify({native_id: m.id(), read: m.readStatus(), flagged: m.flaggedStatus(), mailbox: args.mailbox});
}''',
    "move": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const src = findMailbox(acct, args.mailbox).ref; const m = src.messages.byId(Number(args.native_id));
  const mid = safe(() => m.messageId(), null); const subject = m.subject();
  const dst = findMailbox(acct, args.target).ref;
  Mail.move(m, {to: dst});
  delay(0.6);
  let found = null;
  try { const hits = dst.messages.whose({messageId: mid})(); if (hits.length) found = hits[0].id(); } catch (e) {}
  return JSON.stringify({moved: true, native_id: found, mailbox: args.target, subject_len: String(subject || "").length, message_id: mid});
}''',
    "trash": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const src = findMailbox(acct, args.mailbox).ref; const m = src.messages.byId(Number(args.native_id));
  const mid = safe(() => m.messageId(), null);
  Mail.delete(m);
  delay(0.6);
  let still = false; try { still = src.messages.whose({messageId: mid})().length > 0; } catch (e) {}
  return JSON.stringify({trashed: !still, message_id: mid});
}''',
    "draft": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp();
  let sender = args.sender || null;
  if (!sender && args.account) { try { const acct = findAccount(Mail, args.account); const addrs = acct.emailAddresses(); if (addrs && addrs.length) sender = addrs[0]; } catch (e) {} }
  const om = Mail.OutgoingMessage({subject: args.subject || "", content: args.body || "", visible: false});
  Mail.outgoingMessages.push(om);
  if (sender) { try { om.sender = sender; } catch (e) {} }
  for (const a of (args.to || [])) om.toRecipients.push(Mail.Recipient({address: a}));
  for (const a of (args.cc || [])) om.ccRecipients.push(Mail.Recipient({address: a}));
  let saved = false; try { Mail.save(om); saved = true; } catch (e) { try { om.save(); saved = true; } catch (e2) {} }
  let draftId = null; try { draftId = om.id(); } catch (e) {}
  if (args.send) { Mail.send(om); return JSON.stringify({sent: true, draft_id: draftId}); }
  return JSON.stringify({saved, draft_id: draftId});
}''',
    "find_draft": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp();
  const accts = args.account ? [findAccount(Mail, args.account)] : Mail.accounts();
  const found = []; const scanned = [];
  for (const acct of accts) {
    for (const m of allMailboxes(acct)) {
      if (!/drafts/i.test(m.name)) continue;
      scanned.push(acct.name() + ":" + m.path);
      try { const msgs = m.ref.messages; const subs = msgs.subject(); const ids = msgs.id();
            for (let i = 0; i < subs.length; i++) { if (subs[i] === args.subject) found.push({native_id: ids[i], mailbox: m.path, account: acct.name()}); } } catch (e) {}
    }
  }
  return JSON.stringify({found, scanned});
}''',
    "find_by_message_id": r'''
function run(argv){
  const args = parseArgs(argv); const Mail = mailApp(); const acct = findAccount(Mail, args.account);
  const out = [];
  for (const m of allMailboxes(acct)) {
    if (args.mailboxes && args.mailboxes.length && !args.mailboxes.includes(m.path) && !args.mailboxes.includes(m.name)) continue;
    try { const hits = m.ref.messages.whose({messageId: args.message_id})(); for (const h of hits) out.push({native_id: h.id(), mailbox: m.path, read: h.readStatus(), flagged: h.flaggedStatus()}); } catch (e) {}
    if (out.length >= 5) break;
  }
  return JSON.stringify({found: out});
}''',
}


def _run(script: str, args: dict[str, Any] | None = None, timeout: int = LIST_TIMEOUT) -> dict[str, Any]:
    src = _PRELUDE + _SCRIPTS[script]
    cmd = ["osascript", "-l", "JavaScript", "-e", src, "--", json.dumps(args or {}, ensure_ascii=False)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MailError("provider", "osascript is unavailable on this system", status=500) from exc
    except subprocess.TimeoutExpired as exc:
        raise MailError("timeout", f"Apple Mail did not answer within {timeout}s (script {script})", status=504) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        _raise_for(err)
    try:
        return json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise MailError("provider", f"Unexpected Apple Mail response for {script}", status=502) from exc


def _raise_for(err: str) -> None:
    low = err.lower()
    if "-1743" in err or "not authorized to send apple events" in low or "not permitted" in low:
        raise MailError("automation_denied", "macOS denied PODA permission to control Mail (Automation).", AUTOMATION_REMEDY, status=403)
    if "not_found: account" in low:
        raise MailError("not_found", "That Mail account was not found.", status=404)
    if "not_found: mailbox" in low:
        raise MailError("not_found", "That mailbox was not found in the account.", status=404)
    if "-1728" in err or "can't get object" in low:
        raise MailError("not_found", "Message or mailbox no longer exists in Mail.", status=404)
    if "-600" in err or "application isn't running" in low:
        raise MailError("mail_not_running", "Mail is not running.", "Open Mail, or connect with auto_launch=true.", status=409)
    raise MailError("provider", f"Apple Mail error: {err[:300]}", status=502)


def is_running() -> bool:
    try:
        proc = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, text=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def launch() -> None:
    subprocess.run(["open", "-g", "-a", "Mail"], capture_output=True, timeout=15)


def status(auto_launch: bool = False) -> dict[str, Any]:
    """Probe availability/authorization/accounts. Never raises for the common failure modes."""
    out: dict[str, Any] = {"available": True, "authorized": None, "running": is_running(), "accounts_detected": [], "error": None}
    if not out["running"] and not auto_launch:
        out["authorized"] = None
        out["error"] = "Mail is not running"
        return out
    if not out["running"]:
        launch()
    try:
        data = _run("status", {}, timeout=25)
        out["running"] = bool(data.get("running", True))
        out["authorized"] = True
        for a in data.get("accounts", []):
            addrs = a.get("addresses") or []
            out["accounts_detected"].append({"account_name": a.get("account_name"), "provider_type": a.get("provider_type"),
                                             "address_masked": mask_address(addrs[0]) if addrs else "", "enabled": a.get("enabled")})
    except MailError as exc:
        out["authorized"] = False if exc.kind == "automation_denied" else out["authorized"]
        out["error"] = exc.message
    return out


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
    """Headers come from bulk property arrays (~1 s). Bodies cost ~1 s each in Mail and are serialized by the app,
    so snippets are only fetched for the first `snippet_limit` rows; use `snippets()` progressively for the rest."""
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
    return {"messages": rows, "total": data.get("total", len(rows)), "unread": data.get("unread"), "scanned": data.get("scanned")}


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


def find_draft(account: str | None, subject: str) -> dict[str, Any]:
    """Search Drafts mailboxes (of one account, or all accounts when None) for an exact subject."""
    return _run("find_draft", {"account": account, "subject": subject}, timeout=READ_TIMEOUT)


def find_by_message_id(account: str, message_id: str, mailboxes_filter: list[str] | None = None) -> list[dict[str, Any]]:
    return _run("find_by_message_id", {"account": account, "message_id": message_id, "mailboxes": mailboxes_filter or []}, timeout=READ_TIMEOUT).get("found", [])


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
                    "Create an 'Archive' mailbox in Mail for this account, or use move with an explicit mailbox.", status=404)
