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
