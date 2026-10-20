"""Batch mailbox safety: sender specificity, exclusions, and confirmation gate."""
from __future__ import annotations

from uuid import uuid4
from poda_app.connectors.email import bulk
from poda_app.agent import tools


def test_bulk_preview_strict_sender_and_protected_course(monkeypatch,client):
    monkeypatch.setattr(bulk.service,"search",lambda **kwargs:{
        "messages":[
            {"id":"id1","account_id":"a1","from_name":"MIT Undergraduate Admissions",
             "subject":"Admissions newsletter","snippet":"Read more","read":False,"mailbox":"INBOX"},
            {"id":"id2","account_id":"a1","from_name":"MIT Undergraduate Admissions",
             "subject":"Course 16 schedule change","snippet":"Tomorrow at 9","read":False,"mailbox":"INBOX"},
            {"id":"id3","account_id":"a1","from_name":"MIT Undergraduate Association",
             "subject":"UA festival","snippet":"RSVP","read":False,"mailbox":"INBOX"}
        ],"truncated":False,"errors":[]})
    monkeypatch.setattr(bulk.service,"mailboxes",lambda aid:{
        "mailboxes":[{"name":"INBOX"},{"name":"UA emails"}]})
    preview=bulk.preview("Undergraduate Admissions","UA emails",["course 16","schedule"])
    assert preview["count"]==1
    assert preview["complete"]
    assert preview["candidates"][0]["id"]=="id1"
    assert len(preview["excluded"])==1


def test_bulk_apply_is_only_allowed_with_approved_proposal(monkeypatch,client):
    monkeypatch.setattr(bulk.service,"search",lambda **kwargs:{
        "messages":[{"id":"fakeA","account_id":"a1","from_name":"MIT Undergraduate Admissions",
                      "subject":"General letter","snippet":"Hello","read":False,"mailbox":"INBOX"}],
        "truncated":False,"errors":[]})
    monkeypatch.setattr(bulk.service,"mailboxes",lambda aid:{"mailboxes":[{"name":"UA emails"}]})
    operations=[]
    monkeypatch.setattr(bulk.service,"act",lambda aid,mid,act,mailbox=None:
                        operations.append((mid,act,mailbox)) or {"ok":True})
    preview=bulk.preview("Undergraduate Admissions","UA emails")
    assert operations==[]
    # Mutating tool cannot run outside explicit approval.
    blocked=tools.execute("mail_bulk_apply",{"preview_id":preview["preview_id"]},approved=False)
    assert blocked["receipt"]["outcome"]=="blocked"
    assert operations==[]
    # The service apply path carries a single-use token.
    result=bulk.apply(preview["preview_id"])
    assert result["completed"]==1
    assert operations==[("fakeA","mark_read",None),("fakeA","move","UA emails")]
    try:bulk.apply(preview["preview_id"])
    except ValueError:pass
    else:raise AssertionError("replayed mail operation")


def test_bulk_sender_ambiguity_fails_without_mutating():
    try:bulk.preview("UA","UA emails")
    except ValueError as exc:assert "sender" in str(exc)
    else:raise AssertionError("ambiguous sender accepted")
