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
               "times":["08:00"],"timezone":"UTC","allow_calendar_writes":True}
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
              "schema":{"type":"object","properties":{}}}
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
        assert any(x["reason"]=="requires interactive approval" for x in result["flagged"])
    finally:
        if old is None:tools.TOOLS.pop("write_mock",None)
        else:tools.TOOLS["write_mock"]=old
