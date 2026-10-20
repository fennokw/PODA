"""Review-first batch mailbox actions, with strict matching and per-mailbox preflight.

No mutation occurs during preview. Apply is only exposed through the existing
agent proposal approval gate. We deliberately refuse ambiguous sender aliases.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..runtime.db import connect, now_iso, one
from . import service

MAX_BATCH=50
TTL_MINUTES=20


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mail_bulk_previews (
        id TEXT PRIMARY KEY, sender TEXT NOT NULL, exclusions_json TEXT NOT NULL,
        target_mailbox TEXT NOT NULL, items_json TEXT NOT NULL, status TEXT NOT NULL,
        complete INTEGER NOT NULL, created_at TEXT NOT NULL, outcome_json TEXT)""")


def preview(sender:str,target_mailbox:str,exclude_terms:list[str]|None=None,account_id:str|None=None) -> dict[str,Any]:
    sender=sender.strip()
    target_mailbox=target_mailbox.strip()
    if len(sender)<5 or sender.lower() in {"the ua","ua","mit","the school","university","unknown"}:
        raise ValueError("Identify the specific sender or organization, not an ambiguous alias such as UA.")
    if not target_mailbox or len(target_mailbox)>120 or "/" in target_mailbox or "\\" in target_mailbox:
        raise ValueError("Choose a valid existing mailbox name")
    exclusions=[str(s).lower().strip() for s in (exclude_terms or []) if str(s).strip()][:12]
    searched=service.search(query=sender,account_id=account_id,limit=100,include_body=True)
    seen=set();matched=[];protected=[]
    for msg in searched.get("messages") or []:
        key=(msg.get("account_id"),msg.get("id"))
        if key in seen:continue
        seen.add(key)
        actual=str(msg.get("from_name") or "")
        # Exact organizational phrase in sender name; subject matches alone are
        # NEVER grounds to modify an email. This excludes unrelated UA groups.
        if sender.lower() not in actual.lower():
            continue
        text=(" ".join(str(msg.get(k) or "") for k in ("subject","snippet"))).lower()
        if any(term in text for term in exclusions):
            protected.append({"subject":msg.get("subject"),"sender":actual,"reason":"excluded by content"})
            continue
        matched.append({k:msg.get(k) for k in
                ("id","account_id","subject","from_name","date","mailbox","read")})
    # Do not assert this is all mail when the backend scan is incomplete.
    complete=not searched.get("truncated") and not searched.get("errors")
    if len(matched)>MAX_BATCH:
        matched=matched[:MAX_BATCH]
        complete=False
    # Verify existence of target mailbox in EACH selected account before
    # offering the user a mutation proposal.
    accounts=set(item["account_id"] for item in matched)
    for aid in accounts:
        available=[v["name"] for v in service.mailboxes(aid)["mailboxes"]]
        if not any(target_mailbox.lower()==name.lower() for name in available):
            raise ValueError(f"Target mailbox '{target_mailbox}' is missing in connected account {aid}. Create it in Mail first.")
    pid=str(uuid.uuid4())
    db=connect()
    try:
        ensure_tables(db)
        db.execute("INSERT INTO mail_bulk_previews VALUES (?,?,?,?,?,?,?,?,?)",
                   (pid,sender,json.dumps(exclusions),target_mailbox,json.dumps(matched),
                    "pending",int(complete),now_iso(),None))
        db.commit()
    finally:db.close()
    return {"preview_id":pid,"sender":sender,"target_mailbox":target_mailbox,
            "candidates":matched[:MAX_BATCH],"excluded":protected[:25],"complete":complete,
            "count":len(matched),"errors":searched.get("errors") or [],
            "note":"Preview only. No email has been changed. If complete is false, these are NOT all messages by this sender."}


def apply(preview_id:str) -> dict[str,Any]:
    db=connect()
    try:
        ensure_tables(db)
        row=one(db,"SELECT * FROM mail_bulk_previews WHERE id=?",(preview_id,))
        if not row:raise ValueError("Bulk preview not found")
        created=datetime.fromisoformat(row["created_at"].replace("Z","+00:00"))
        if datetime.now(timezone.utc)-created>timedelta(minutes=TTL_MINUTES):
            raise ValueError("Preview expired; repeat the search before approving changes")
        if row["status"]!="pending":
            raise ValueError("This preview was already applied or consumed")
        items=json.loads(row["items_json"])
        if not items:raise ValueError("Nothing matched the sender/exclusion conditions")
        # It is safer to reject incomplete scans for "all messages" than silently
        # imply comprehensive coverage.
        if not row["complete"]:
            raise ValueError("Search was incomplete or exceeded the batch limit. Narrow the scope before changing mail.")
        db.execute("UPDATE mail_bulk_previews SET status='started' WHERE id=? AND status='pending'",(preview_id,))
        db.commit()
    finally:db.close()
    completed=[];failed=[]
    for msg in items:
        try:
            if not msg.get("read"):
                service.act(msg["account_id"],msg["id"],"mark_read")
            service.act(msg["account_id"],msg["id"],"move",mailbox=row["target_mailbox"])
            completed.append({"id":msg["id"],"subject":msg.get("subject")})
        except Exception as exc:
            failed.append({"id":msg["id"],"error":str(exc)[:260]})
    db=connect()
    try:
        ensure_tables(db)
        db.execute("UPDATE mail_bulk_previews SET status=?,outcome_json=? WHERE id=?",
                   ("completed" if not failed else "partial_failure",json.dumps({"completed":completed,"failed":failed}),preview_id))
        db.commit()
    finally:db.close()
    return {"completed":len(completed),"failures":failed,
            "target_mailbox":row["target_mailbox"],
            "note":"Only the verified preview candidates were modified; any failures are listed."}
