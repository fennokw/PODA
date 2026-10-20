"""PODA v0.9 regression tests: intent context, model configuration and daily slots."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from poda_app.agent import gate
from poda_app.runtime import models
from poda_app.daily_updates import service
from poda_app.runtime import model_management


def test_mail_folder_does_not_trigger_filesystem_block():
    utterance='Mark all of those emails as read and move them to the "UA emails" folder.'
    intents=gate.requested_action_intents(utterance)
    assert "email_modify" in intents
    assert not any(v.startswith("file_") for v in intents)
    snapshot={"filesystem":{"general_user_file_read":False,"general_user_file_edit":False,
               "general_user_file_create":False},
              "email":{"can_modify_mailbox":True}}
    assert gate.unsupported_action_response(utterance,snapshot) is None


def test_explicit_local_file_request_is_still_blocked():
    text="Edit the notes.txt file on my Desktop"
    assert "file_edit" in gate.requested_action_intents(text)


def test_arbitrary_installed_chat_model_assignment(monkeypatch,client):
    monkeypatch.setattr(model_management.ollama,"installed_models",lambda:["custom/chat:Q4_K_M","nomic-embed-text:latest"])
    save=client.put("/system/model-manager/profile",json={"profile":"balanced","model":"custom/chat:Q4_K_M"})
    assert save.status_code==200,save.text
    registry=models.registry(["custom/chat:Q4_K_M","nomic-embed-text:latest"],32)
    assert registry["profiles"]["balanced"]["model"]=="custom/chat:Q4_K_M"
    reject=client.put("/system/model-manager/profile",json={"profile":"deep","model":"uninstalled:1b"})
    assert reject.status_code==422
    assert any(c["tag"]=="qwen3.6:27b-q4_K_M" for c in registry["qwen36"])


def test_secure_install_terminal_argument_and_no_automatic_pull(monkeypatch,client,tmp_path):
    monkeypatch.setattr(model_management.platform,"system",lambda:"Darwin")
    monkeypatch.setattr(model_management.config,"DATA_DIR",tmp_path)
    seen=[]
    class FakeProcess:
        pass
    monkeypatch.setattr(model_management.subprocess,"Popen",lambda args,**kwargs:seen.append(args) or FakeProcess())
    out=client.post("/system/model-manager/install-terminal",json={"tag":"qwen3.6:27b-q4_K_M"})
    assert out.status_code==200,out.text
    assert seen and seen[0][:3]==["open","-a","Terminal"]
    script=(tmp_path/"model_install_scripts").glob("*.command")
    content=next(script).read_text()
    assert "ollama pull qwen3.6:27b-q4_K_M" in content
    bad=client.post("/system/model-manager/install-terminal",json={"tag":"something; rm -rf ~"})
    assert bad.status_code==422


def test_daily_schedules_and_once_only_claim(client):
    payload={"title":"Daily regression","prompt":"Summarize my current work priorities.",
             "times":["08:00","20:00"],"timezone":"UTC","enabled":True}
    created=client.post("/daily-updates",json=payload)
    assert created.status_code==200,created.text
    sid=created.json()["schedule"]["id"]
    try:
        today=datetime.now(timezone.utc).date()
        when=datetime(today.year,today.month,today.day,8,0,tzinfo=timezone.utc)
        # If same-day 08:00 is in the future, choosing yesterday would violate
        # the 10-minute grace; use fixed mock clock and manually validate claim.
        new=service.claim_due(when+timedelta(minutes=2))
        assert len([r for r in new if r["task"]["id"]==sid])==1
        again=service.claim_due(when+timedelta(minutes=2))
        assert not [r for r in again if r["task"]["id"]==sid]
        missed=service.claim_due(when+timedelta(hours=1))
        assert not [r for r in missed if r["task"]["id"]==sid]
        listing=client.get("/daily-updates").json()
        assert any(p["id"]==sid for p in listing["schedules"])
        assert any(r["prompt_id"]==sid for r in listing["runs"])
        toggle=client.put("/daily-updates/"+sid,json={**payload,"enabled":False})
        assert toggle.status_code==200
        assert toggle.json()["schedule"]["enabled"] is False
    finally:
        assert client.delete("/daily-updates/"+sid).json()["deleted"]


def test_timezone_and_validation():
    with pytest.raises(ValueError):service.clean_times(["07:00","07:00"])
    with pytest.raises(ValueError):service.clean_times(["27:90"])
    with pytest.raises(ValueError):service.clean_timezone("Not/AZone")
    # Full minute precision in a DST zone, not converted to server time.
    row={"timezone":"America/New_York","times_json":'["09:00"]'}
    now=datetime(2026,10,20,9,1,tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    slots=service._slots(row,now)
    assert len(slots)==1
    assert slots[0][0]=="2026-10-20@09:00"
