"""Regression checks for PODA v0.9.2 additive local capabilities."""
from __future__ import annotations

import io
import json
import zipfile

from poda_app.runtime import model_catalog
from poda_app.daily_updates import service
from poda_app.memory import hindsight_bridge


def test_shared_gpu_does_not_double_count_ram():
    hw={"unified_memory_gb":24,"ram_total_gb":24,"memory_architecture":"unified",
        "gpu_memory_dedicated_gb":20,"disk_free_gb":150}
    result=model_catalog.assess(hw,[])
    assert result["hardware"]["gpu_shared_with_ram"]
    assert result["hardware"]["bottleneck"]=="shared unified RAM/GPU"
    assert result["hardware"]["available_model_budget_gb"] < 24
    big=next(x for x in result["families"] if x["family"]=="Qwen3-Coder-Next")
    assert big["recommended_tag"] is None


def test_model_catalog_contains_three_local_roles():
    result=model_catalog.assess({"unified_memory_gb":64,"memory_architecture":"unified",
                                "disk_free_gb":150},[])
    assert {"coding","general","vision"} <= {x["role"] for x in result["families"]}
    assert not any("http" in v["tag"] for x in result["families"] for v in x["variants"])


def test_multi_account_automation_selection(client,monkeypatch):
    from poda_app.connectors.email import state
    fake=[{"id":"a","enabled":1},{"id":"b","enabled":1}]
    monkeypatch.setattr(state,"list_accounts",lambda:fake)
    payload={"title":"Both inboxes","prompt":"Review messages from both emails",
             "times":["09:30"],"timezone":"UTC","mail_account_ids":["a","b"]}
    response=client.post("/daily-updates",json=payload)
    assert response.status_code==200,response.text
    task=response.json()["schedule"]
    try:
        assert task["mail_account_ids"]==["a","b"]
        listing=client.get("/daily-updates").json()["schedules"]
        assert any(t["id"]==task["id"] and t["mail_account_ids"]==["a","b"] for t in listing)
        bad=client.put("/daily-updates/"+task["id"],json={**payload,"mail_account_ids":["a","missing"]})
        assert bad.status_code==422
    finally:
        client.delete("/daily-updates/"+task["id"])


def test_skills_upload_disabled_then_enable(client):
    response=client.post("/personalize/skills",
                         files={"file":("review.md",b"# Review code\nInspect symbols, run tests, verify outcomes.", "text/markdown")},
                         data={"name":"Python review","category":"coding"})
    assert response.status_code==200,response.text
    skill=response.json()
    try:
        assert skill["enabled"] is False
        from poda_app.runtime.skill_library import hint_for_task
        assert not hint_for_task("review Python code",coding=True)
        result=client.put("/personalize/skills/"+skill["id"],json={"enabled":True})
        assert result.status_code==200
        assert "Inspect symbols" in hint_for_task("review Python code",coding=True)
    finally:
        client.delete("/personalize/skills/"+skill["id"])


def test_zip_skill_is_bounded_and_non_executable(client):
    mem=io.BytesIO()
    with zipfile.ZipFile(mem,"w") as z:
        z.writestr("skill/SKILL.md","# Safe context\nOnly instruct review and local tests.")
        z.writestr("skill/payload.py","raise RuntimeError('DO NOT EXECUTE')")
    r=client.post("/personalize/skills",
       files={"file":("code-skill.zip",mem.getvalue(),"application/zip")},
       data={"name":"Review-only","category":"harness"})
    assert r.status_code==200,r.text
    client.delete("/personalize/skills/"+r.json()["id"])


def test_hindsight_feedback_uses_existing_node_ids_only(client):
    from poda_app.runtime.db import connect,now_iso
    node_id="integration-memory-synthetic"
    db=connect()
    try:
        db.execute("""INSERT OR IGNORE INTO memory_nodes
             (id,title,node_type,summary,content,created_at,updated_at)
             VALUES (?,?,'memory',?,?,?,?)""",
             (node_id,"Synthetic test note","Synthetic","Synthetic",now_iso(),now_iso()))
        db.commit()
    finally: db.close()
    try:
        res=client.post("/memory/hindsight/feedback",json={"node_id":node_id,"assessment":"incorrect","reason":"test"})
        assert res.status_code==200,res.text
        db=connect()
        try:
            hindsight_bridge.ensure_tables(db)
            assert node_id not in {n["id"] for n in hindsight_bridge._canonical_nodes(db)}
        finally:db.close()
        res=client.post("/memory/hindsight/feedback",json={"node_id":node_id,"assessment":"restore"})
        assert res.status_code==200
    finally:
        db=connect()
        try:
            db.execute("DELETE FROM poda_memory_feedback WHERE poda_node_id=?",(node_id,))
            db.execute("DELETE FROM memory_nodes WHERE id=?",(node_id,))
            db.commit()
        finally:db.close()


def test_hindsight_no_cloud_calls_when_disabled(monkeypatch):
    monkeypatch.setattr(hindsight_bridge,"enabled",lambda:False)
    monkeypatch.setattr(hindsight_bridge,"_request",lambda *a,**k: (_ for _ in ()).throw(AssertionError("unexpected HTTP")))
    assert hindsight_bridge.recall_node_ids("project") == []
    assert hindsight_bridge.sync_once()=={"enabled":False,"synchronized":0,"deleted":0}


def test_code_context_requires_existing_grant(tmp_path):
    from poda_app.agent.code_context import inspect_code
    p=tmp_path/"example.py"
    p.write_text("def update_calendar():\n    return True\n")
    result=inspect_code(str(p))
    assert not result["ok"]
