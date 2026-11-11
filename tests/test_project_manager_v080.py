"""Regression tests for unified projects, stored documents, calibration and rolling chat context."""
from __future__ import annotations

from uuid import uuid4

from poda_app.memory import store
from poda_app.runtime.db import connect, one
from poda_app.chat.working_context import recent
from poda_app.chat import mail_fulfillment


def test_project_manager_crud_and_attachment(client):
    name = "PM test " + uuid4().hex[:7]
    created = client.post("/project-manager/projects", json={"name": name, "notes": "Ship a first version"})
    assert created.status_code == 200, created.text
    p = created.json()
    project_id = p["id"]
    try:
        assert p["name"] == name
        assert client.get("/project-manager/projects/" + project_id).status_code == 200
        updated = client.patch("/project-manager/projects/" + project_id, json={
            "name": name + " revised", "progress": 35})
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == name + " revised"
        doc = client.post(f"/project-manager/projects/{project_id}/attachments",
                          files={"file": ("assignment.txt", b"1. Solve problem one.\n2. Review solutions.", "text/plain")})
        assert doc.status_code == 200, doc.text
        attachment_id = doc.json()["attachment"]["id"]
        listing = client.get("/project-manager/projects/" + project_id).json()
        assert len(listing["attachments"]) == 1
        assert "Solve problem one" in client.get(
            f"/project-manager/projects/{project_id}/attachments/{attachment_id}/text").json()["text"]
        downloaded = client.get(f"/project-manager/projects/{project_id}/attachments/{attachment_id}/download")
        assert downloaded.status_code == 200 and downloaded.content.startswith(b"1. Solve")
        assert client.delete(f"/project-manager/projects/{project_id}/attachments/{attachment_id}").json()["removed"]
        # No arbitrary linked paths without an active user folder grant.
        link = client.post(f"/project-manager/projects/{project_id}/links", json={"path": "/etc/hosts"})
        assert link.status_code == 403
    finally:
        assert client.delete("/project-manager/projects/" + project_id).status_code == 200


def test_project_rename_retains_linked_workload(client):
    name = "Renamable " + uuid4().hex[:6]
    p = client.post("/project-manager/projects", json={"name": name}).json()
    project_id = p["id"]
    task_id = None
    try:
        task = client.post("/workload",json={"title": "Write implementation tests", "project": name,
                                           "category": "coding", "user_minutes": 85})
        assert task.status_code == 200, task.text
        task_id = task.json()["id"]
        changed = client.patch("/project-manager/projects/" + project_id, json={"name": name + " changed"})
        assert changed.status_code == 200
        assert changed.json()["task_count"] >= 1
        assert any(t["id"] == task_id for t in changed.json()["tasks"])
    finally:
        if task_id:
            client.delete("/workload/" + task_id)
        client.delete("/project-manager/projects/" + project_id)


def test_pace_personalization_samples(client):
    before=client.get("/workload/pace")
    assert before.status_code==200, before.text
    uploaded=client.post("/workload/pace/examples",
        data={"title":"Sample design assignment","category":"creative","difficulty":"6",
              "user_minutes":"120","baseline_minutes":"60","description":"Sketch several variations"},
        files={"file":("brief.txt",b"Design mockups; compare alternatives.", "text/plain")})
    assert uploaded.status_code==200,uploaded.text
    e=uploaded.json()["example"]
    try:
        assert e["filename"]=="brief.txt"
        assert client.get("/workload/pace").status_code==200
        assert uploaded.json()["calibration"]["examples"] >= 1
        assert uploaded.json()["calibration"]["multiplier"] > 1.0
        download=client.get("/workload/pace/examples/"+e["id"]+"/download")
        assert download.content.startswith(b"Design mockups")
    finally:
        assert client.delete("/workload/pace/examples/"+e["id"]).json()["deleted"]


def test_working_context_is_cheap_and_graduates_to_graph():
    session_id=str(uuid4())
    first=store.save_message("user", "I asked about a deadline", session_id=session_id, defer_memory=True)
    second=store.save_message("assistant", "I checked the assignment.", session_id=session_id, defer_memory=True)
    db=connect()
    try:
        assert one(db,"SELECT id FROM memory_nodes WHERE source_message_id=?",(first,)) is None
    finally: db.close()
    context=recent(session_id)
    assert "deadline" in context and "I checked the assignment" in context
    assert "deadline" in recent(session_id,exclude_message_id=second)
    result=store.flush_session_to_graph(session_id)
    assert result["promoted"] >= 1
    db=connect()
    try:
        node=one(db,"SELECT * FROM memory_nodes WHERE source_message_id=?",(first,))
        assert node and node["response_message_id"]==second
        assert node["memory_kind"] in {"exchange","durable"}
    finally:db.close()


def test_mail_results_become_an_actual_answer(monkeypatch):
    params={"query":"","start_date":"2026-10-13","end_date":"2026-10-20"}
    response={"receipt":{"id":"test","outcome":"succeeded","verification":"provider read"},
      "result":{"ok":True,"result":{"scanned_headers":12,"read_bodies":2,"truncated":False,
       "errors":[],"messages":[{"subject":"Deadline for proposal", "from_name":"Coordinator",
       "date":"2026-10-18","snippet":"Please submit the proposal by Friday","read":False}]}}}
    monkeypatch.setattr(mail_fulfillment.tools,"execute",lambda *args,**kwargs:response)
    monkeypatch.setattr(mail_fulfillment,"parameters",lambda *args,**kwargs:params)
    _,_,answer=mail_fulfillment.fulfill("Find important emails from past week",None)
    assert "Deadline for proposal" in answer
    assert "Search completed" in answer
    assert "I'll search" not in answer
    assert "Action requested" in answer or "Task / deadline" in answer
    assert not mail_fulfillment.is_mail_request("Send this email", None)


def test_mail_zero_results_do_not_claim_complete_if_truncated():
    body=mail_fulfillment.report({"start_date":"2026-10-13","end_date":"2026-10-20"},
      {"receipt":{"outcome":"succeeded"},"result":{"ok":True,"result":{
        "scanned_headers":1576,"read_bodies":0,"messages":[],"truncated":True,"errors":[]}}})
    assert "Coverage limitation" in body
    assert "No messages were returned" in body
    assert "no important emails" in body


def test_top_five_mail_review_routes_every_account_and_rolling_dates(monkeypatch):
    from datetime import datetime, timezone
    from poda_app.chat import mail_fulfillment as mail
    request = 'Give me the top 5 most important things in my inbox from the past 3 days from all of my emails.'
    now = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
    assert mail.is_mail_request(request)
    args_seen = []
    messages = [
        {'id': 'a', 'account_label': 'Google', 'subject': 'Research deadline', 'from_name': 'Tutor', 'date': '2026-11-11T15:00:00+00:00', 'snippet': 'Please submit your proposal by Friday', 'body_read': True},
        {'id': 'b', 'account_label': 'Exchange', 'subject': 'Team meeting', 'from_name': 'Coach', 'date': '2026-11-10T15:00:00+00:00', 'snippet': 'Meeting tomorrow at noon', 'body_read': True},
        {'id': 'c', 'account_label': 'Google', 'subject': 'Old offer', 'date': '2026-11-08T17:59:59+00:00', 'snippet': 'Old'},
        {'id': 'd', 'account_label': 'Exchange', 'subject': 'Unknown date', 'snippet': 'Unknown'},
    ]
    def execute(name, args, **kwargs):
        args_seen.append((name, args.copy()))
        return {'receipt': {'id': 'receipt', 'outcome': 'succeeded'}, 'result': {'ok': True, 'result': {'scanned_headers': 200, 'read_bodies': 2, 'truncated': True, 'errors': ['partial'], 'messages': messages}}}
    monkeypatch.setattr(mail.tools, 'execute', execute)
    monkeypatch.setattr(mail.models, 'registry', lambda: {'profiles': {'fast': {'model': 'test'}}})
    prompts = []
    def rank(prompt, *args, **kwargs):
        prompts.append(prompt)
        return {'message': {'content': '["1", "0", "999"]'}}
    monkeypatch.setattr(mail.inference, 'chat', rank)
    params, _, result = mail.fulfill(request, 'session', now=now)
    assert args_seen[0][0] == 'mail_search' and 'account_id' not in args_seen[0][1]
    assert args_seen[0][1]['query'] == '' and args_seen[0][1]['related_terms'] == []
    assert args_seen[0][1]['inbox_only'] and args_seen[0][1]['start_date'] == '2026-11-07'
    assert 'Research deadline' in result and 'Team meeting' in result
    assert 'Google' in result and 'Exchange' in result and 'Old offer' not in result and 'Unknown date' not in result
    assert 'since 2026-11-08 18:00 UTC' in result and 'Coverage limitation' in result
    assert 'unknown date' in result and 'I will search' not in result
    assert len(prompts) == 1


def test_mail_rank_rejects_invented_candidates(monkeypatch):
    from poda_app.chat import mail_fulfillment as mail
    messages = [{'subject': 'Real deadline', 'snippet': 'action required', 'date': '2026-11-11T00:00:00+00:00'}]
    monkeypatch.setattr(mail.models, 'registry', lambda: {'profiles': {'fast': {'model': 'test'}}})
    monkeypatch.setattr(mail.inference, 'chat', lambda *args, **kwargs: {'message': {'content': '["900", "-1", "malicious invented email"]'}})
    assert mail._local_rank(messages, 'Top 5', 5) == messages


def test_inbox_search_checks_each_connected_account_only_inbox(monkeypatch):
    from poda_app.connectors.email import service
    accounts = [
        {'id': 'native', 'provider': service.NATIVE_PROVIDER, 'account_name': 'Exchange', 'address_masked': 'e***@example.edu'},
        {'id': 'gmail', 'provider': 'gmail_imap', 'account_name': 'Google', 'address': 'x@example.com', 'address_masked': 'x***@example.com'},
    ]
    calls = []
    monkeypatch.setattr(service.state, 'list_accounts', lambda: accounts)
    monkeypatch.setattr(service, '_require_read', lambda account: None)
    monkeypatch.setattr(service, '_touch', lambda *args: None)
    monkeypatch.setattr(service.native_mail, 'mailboxes', lambda *args: (_ for _ in ()).throw(AssertionError('archive discovery should not run')))
    def listing(name, account, mailbox, **kwargs):
        calls.append((account, mailbox))
        return {'messages': [{'id': f'{account}-message', 'account_id': account, 'date': '2026-11-10T12:00:00+00:00', 'subject': 'Meeting'}], 'total': 1}
    monkeypatch.setattr(service.native_mail, 'list_messages', listing)
    monkeypatch.setattr(service.gmail_imap, 'list_messages', listing)
    result = service.search('', start_date='2026-11-08', end_date='2026-11-11', inbox_only=True)
    assert calls == [('native', 'INBOX'), ('gmail', 'INBOX')]
    assert {m['account_label'] for m in result['messages']} == {'e***@example.edu', 'x***@example.com'}
    assert not result['errors']


def test_rolling_window_filters_before_prioritization_and_body_reads(monkeypatch):
    from poda_app.connectors.email import service
    account = {'id': 'a', 'provider': 'gmail_imap', 'account_name': 'Google', 'address': 'x@example.com', 'address_masked': 'x***@example.com'}
    monkeypatch.setattr(service.state, 'list_accounts', lambda: [account])
    monkeypatch.setattr(service, '_require_read', lambda account: None)
    monkeypatch.setattr(service, '_touch', lambda *args: None)
    messages = [
        {'id': 'old', 'account_id': 'a', 'date': '2026-11-08T17:59:59+00:00', 'subject': 'Urgent deadline', 'read': False},
        {'id': 'new', 'account_id': 'a', 'date': '2026-11-10T00:00:00+00:00', 'subject': 'Project update', 'read': True},
        {'id': 'unknown', 'account_id': 'a', 'date': None, 'subject': 'Urgent deadline'},
    ]
    monkeypatch.setattr(service.gmail_imap, 'list_messages', lambda *args, **kwargs: {'messages': messages, 'total': 3})
    reads = []
    monkeypatch.setattr(service, 'read_message', lambda account, msg: (reads.append(msg) or {'body_text': 'Details'}))
    result = service.search('', start_date='2026-11-07', end_date='2026-11-11', inbox_only=True,
                            prioritize=True, include_body=True, limit=1,
                            received_after='2026-11-08T18:00:00+00:00', received_before='2026-11-11T18:00:00+00:00')
    assert [row['id'] for row in result['messages']] == ['new']
    assert reads == ['new']
    assert result['excluded_undated'] == 1
