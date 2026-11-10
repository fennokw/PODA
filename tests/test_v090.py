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


def test_role_assignment_uses_installed_gguf(monkeypatch, client, tmp_path):
    catalog = model_management.model_catalog
    monkeypatch.setattr(catalog, "root", lambda: tmp_path / "models")
    model_path = tmp_path / "Fixture-Q4_K_M.gguf"
    model_path.write_bytes(b"GGUF" + b"fixture")
    item = catalog.import_model(str(model_path), thinking=True, tools=True)
    visual = catalog.import_model(str(model_path), str(model_path))
    roles = {"balanced": item["id"], "fast_coding": item["id"], "deep_coding": item["id"], "vision": visual["id"]}
    try:
        for role, name in roles.items():
            response = client.put("/system/model-manager/profile", json={"profile": role, "model": name})
            assert response.status_code == 200, response.text
        result = models.registry()
        assert set(result["profiles"]) == set(models.PROFILE_NAMES)
        assert all(result["profiles"][role]["model"] == name for role, name in roles.items())
        assert client.put("/system/model-manager/profile", json={"profile": "vision", "model": item["id"]}).status_code == 422
        assert client.put("/system/model-manager/profile", json={"profile": "deep", "model": "not-installed"}).status_code == 422
    finally:
        for role in roles:
            models.set_profile(role, "", [])


def test_catalog_blocks_unverified_download_and_invalid_cache_setting(client):
    assert client.post("/system/model-manager/download", json={"model": "../outside.gguf"}).status_code == 422
    settings = model_management.inference.settings()
    response = client.put("/system/model-manager/settings", json={**settings, "cache_v": "q4_0", "flash_attention": "off"})
    assert response.status_code == 422
    assert model_management.inference.settings() == settings


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
