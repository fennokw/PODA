"""Regression coverage for daily automation, isolated conversations, and simplified planning."""
from datetime import datetime, timezone
import pytest
from poda_app.daily_updates import service
from poda_app.workload import service as workload
from poda_app.agent import orchestrator


def test_schedule_default_cannot_write_to_notion(client):
    payload = {"title":"Safe task","prompt":"Review recent emails","times":["08:00"],"timezone":"UTC"}
    made = client.post("/daily-updates", json=payload)
    assert made.status_code == 200, made.text
    task = made.json()["schedule"]
    assert not task.get("calendar_write_target")
    client.delete("/daily-updates/"+task["id"])


def test_unverified_background_write_is_rejected(client):
    payload = {"title":"No bypass","prompt":"Create a calendar event",
               "times":["08:00"],"timezone":"UTC","permissions":[{"tool":"notion_calendar_create","scope":{}}]}
    result = client.post("/daily-updates", json=payload)
    assert result.status_code == 422


def test_personalized_daily_allowances(client):
    result = client.put("/workload/settings/preferences",
        json={"sleep_hours":8,"meal_minutes":90,"free_minutes":120})
    assert result.status_code == 200, result.text
    p = client.get("/workload/settings/preferences").json()
    assert (p["sleep_hours"],p["meal_minutes"],p["free_minutes"]) == (8,90,120)
    invalid=client.put("/workload/settings/preferences",
        json={"sleep_hours":15,"meal_minutes":90,"free_minutes":120})
    assert invalid.status_code == 422


def test_no_approval_proposal_for_unattended_writes(monkeypatch):
    # Unattended planning should never create interactive approval proposals.
    from poda_app.agent import tools
    fake_tool={"description":"safe mock","mutating":True,"requires_confirmation":True,
              "needs":None,"handler":lambda:{"ok":True,"result":{}},
              "schema":{"type":"object","properties":{},"required":[]}}
    old=tools.TOOLS.get("write_mock")
    tools.TOOLS["write_mock"]=fake_tool
    class OllamaStub:
        @staticmethod
        def chat(*args,**kwargs):
            return {"message":{"tool_calls":[{"function":{"name":"write_mock","arguments":{}}}]}}
    monkeypatch.setattr(orchestrator.ollama,"chat",OllamaStub.chat)
    monkeypatch.setattr(orchestrator.ollama,"status",lambda:{"models":["fake"]})
    monkeypatch.setattr(orchestrator,"_planner_model",lambda *args:"fake")
    try:
        result=orchestrator.plan("write something",{},unattended=True)
        assert result["proposal_id"] is None
        assert not result["executed"]
        assert any(x["reason"].startswith("requires interactive approval") for x in result["flagged"])
    finally:
        if old is None:tools.TOOLS.pop("write_mock",None)
        else:tools.TOOLS["write_mock"]=old


def test_scoped_permission_rejects_unrelated_actions(monkeypatch, client):
    from poda_app.daily_updates import permissions
    monkeypatch.setattr(permissions, "prepare", lambda grants: grants)
    req={"title":"Morning task","prompt":"Do approved work","times":["06:30"],"timezone":"UTC",
         "permissions":[{"tool":"mail_mark","scope":{"account_id":"acct","action":"mark_read"}}]}
    made=client.post("/daily-updates",json=req)
    assert made.status_code==200, made.text
    task=made.json()["schedule"]
    sid=task["id"]
    try:
        monkeypatch.setattr(permissions, "_matches", lambda policy,name,args:
            name=="mail_mark" and args.get("action")=="mark_read" and args.get("account_id")=="acct")
        monkeypatch.setattr(permissions, "PERMISSION_SPECS", {**permissions.PERMISSION_SPECS,"mail_mark":("Mark",{"account_id":"Account","action":"Action"})})
        monkeypatch.setattr(permissions, "authorize", permissions.authorize)
        from poda_app.agent import tools
        monkeypatch.setattr(tools,"availability",lambda *a,**k:{"mail_mark":{"available":True}})
        ok,_=permissions.authorize(sid,task["updated_at"],"mail_mark",{"account_id":"acct","action":"mark_read"})
        assert ok
        ok,_=permissions.authorize(sid,task["updated_at"],"mail_mark",{"account_id":"other","action":"mark_read"})
        assert not ok
        ok,_=permissions.authorize(sid,"stale-revision","mail_mark",{"account_id":"acct","action":"mark_read"})
        assert not ok
    finally:
        client.delete("/daily-updates/"+sid)


def test_compound_mail_to_calendar_does_not_short_circuit():
    from poda_app.chat.mail_fulfillment import is_mail_request, is_compound_request
    phrase = "Review my emails for upcoming meetings and add confirmed meetings to my Notion calendar"
    assert is_compound_request(phrase)
    assert not is_mail_request(phrase)
    assert is_mail_request("Review my emails from the past week")


def test_unattended_planner_continues_after_mail_read(monkeypatch, client):
    from poda_app.agent import tools
    from poda_app.daily_updates import permissions
    requested = []
    responses = iter([
        {"message":{"tool_calls":[{"function":{"name":"mail_search","arguments":{"query":"meeting"}}}]}},
        {"message":{"tool_calls":[{"function":{"name":"notion_calendar_create","arguments":{
            "title":"Confirmed meeting","start":"2026-10-21T15:00:00-04:00"}}}]}},
        {"message":{"content":"Both steps completed.","tool_calls":[]}},
    ])
    monkeypatch.setattr(orchestrator.ollama,"chat",lambda *a,**k:next(responses))
    monkeypatch.setattr(orchestrator,"_planner_model",lambda *a:"fake")
    monkeypatch.setattr(permissions,"authorize",lambda *a:(True,"test-approved scope"))
    def fake_execute(name,args,approved=False,session_id=None):
        requested.append((name,approved))
        return {"receipt":{"id":str(len(requested)),"outcome":"succeeded","verification":"test"},
                "result":{"ok":True,"result":{"verified":True,"data":name},"error":None}}
    monkeypatch.setattr(tools,"execute",fake_execute)
    result=orchestrator.plan("Review my emails and add confirmed meetings to my calendar",{},
                             unattended=True,unattended_target="test-id",unattended_revision="revision")
    assert requested==[("mail_search",False),("notion_calendar_create",True)]
    assert result["proposal_id"] is None
    assert not [f for f in result["flagged"] if f["name"]=="calendar-followup"]


def test_mail_catalog_uses_existing_nonsecret_accounts(client, monkeypatch):
    from poda_app.connectors.email import state
    monkeypatch.setattr(state,"list_accounts",lambda:[{"id":"saved-id","provider":"apple_mail",
         "account_name":"MIT Mail","address_masked":"k***@mit.edu",
         "allow_modify":1,"allow_send":0,"enabled":1}])
    response=client.get("/daily-updates/permissions/catalog")
    assert response.status_code==200
    assert response.json()["mail_accounts"][0]["id"]=="saved-id"
    assert "password" not in str(response.json()).lower()
