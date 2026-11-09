import json

from poda_app.agent import grants


def _events(text: str):
    out = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        ev = next((l[6:].strip() for l in lines if l.startswith("event:")), None)
        data = next((l[5:].strip() for l in lines if l.startswith("data:")), None)
        if ev and data:
            out.append((ev, json.loads(data)))
    return out


def test_chat_emits_tool_and_proposal_events(client, monkeypatch, tmp_folder):
    from poda_app.chat import router as chat_router
    from poda_app.agent import orchestrator
    (tmp_folder / "notes.md").write_text("alpha beta")
    g = grants.add_grant(str(tmp_folder), "edit", "chat-test")
    try:
        plans = iter([
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "fs_read", "arguments": {"path": str(tmp_folder / "notes.md")}}},
                {"function": {"name": "fs_write", "arguments": {"path": str(tmp_folder / "copy.md"), "content": "alpha beta"}}}]}},
        ])
        monkeypatch.setattr(orchestrator.ollama, "chat", lambda *a, **k: next(plans))
        monkeypatch.setattr(orchestrator.ollama, "status", lambda: {"reachable": True, "models": ["llama3.2:3b"]})

        async def fake_stream(messages, model, options=None, tools=None, think=None, timeout=900):
            system = messages[0]["content"]
            assert "PENDING PROPOSAL" in system and "TOOL RESULTS" in system and "TOOLS (PODA executes" in system
            yield {"message": {"content": "I will copy notes.md once you approve."}, "done": False}
            yield {"message": {"content": ""}, "done": True, "eval_count": 5, "eval_duration": 1000}
        monkeypatch.setattr(chat_router.ollama, "chat_stream", fake_stream)
        monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {"model": "llama3.2:3b", "profile": "fast", "num_ctx": 8192, "options": {}, "think": False})
        monkeypatch.setattr(chat_router.retrieval, "recall", lambda q, limit=18, **kw: {"mode": "test", "weights": {}, "results": [], "depth_used": kw.get("depth", "auto"), "surfaces": []})
        r = client.post("/chat/stream", json={"message": f"Read notes.md in {tmp_folder} and create copy.md with the same text", "mode": "fast"})
        events = _events(r.text)
        kinds = [e for e, _ in events]
        assert "tool" in kinds and "proposal" in kinds and "done" in kinds
        tool_ev = next(d for e, d in events if e == "tool")
        assert tool_ev["name"] == "fs_read" and tool_ev["outcome"] == "succeeded" and tool_ev["receipt_id"]
        proposal = next(d for e, d in events if e == "proposal")
        session_id = next(d["session_id"] for e, d in events if e == "session")
        history_url = f"/chat/session/{session_id}/messages"
        assert proposal["steps"][0]["tool"] == "fs_write" and not (tmp_folder / "copy.md").exists()
        saved = client.get(history_url).json()
        activity = saved["messages"][-1]["workflow_activity"]
        assert [entry["event"] for entry in activity if entry["event"] != "status"] == ["recall", "tool", "proposal", "done"]
        assert next(entry for entry in activity if entry["event"] == "tool")["receipt_id"] == tool_ev["receipt_id"]
        assert str(tmp_folder) not in json.dumps(activity) and "alpha beta" not in json.dumps(activity)
        assert saved["workflows"][0]["status"] == "pending"
        assert any(item["session_id"] == session_id for item in client.get("/chat/sessions").json()["sessions"])
        assert client.get("/chat/session/unrelated-session/messages").json()["workflows"] == []
        approved = client.post(f"/agent/proposals/{proposal['proposal_id']}/approve").json()
        assert approved["status"] == "executed" and (tmp_folder / "copy.md").read_text() == "alpha beta"
        assert client.get(history_url).json()["workflows"][0]["receipts"][0]["outcome"] == "succeeded"

        async def report_stream(messages, model, options=None, tools=None, think=None, timeout=900):
            assert "ACTION RECEIPTS" in messages[0]["content"] and "fs_write" in messages[0]["content"]
            yield {"message": {"content": "Done per receipt."}, "done": True}
        monkeypatch.setattr(chat_router.ollama, "chat_stream", report_stream)
        r2 = client.post("/chat/stream", json={"message": "Report what you did", "continuation_of": proposal["proposal_id"]})
        assert any(e == "done" for e, _ in _events(r2.text))

        async def continue_stream(messages, *args, **kwargs):
            assert "PENDING PROPOSAL" in messages[0]["content"]
            assert "ACTION RECEIPTS" in messages[0]["content"]
            yield {"message": {"content": "Ready to continue."}, "done": True}

        monkeypatch.setattr(chat_router.ollama, "chat_stream", continue_stream)
        monkeypatch.setattr(orchestrator.ollama, "chat", lambda *a, **kw: {"message": {"tool_calls": [
            {"function": {"name": "fs_write", "arguments": {"path": str(tmp_folder / "copy.md"), "content": "alpha beta"}}},
            {"function": {"name": "fs_write", "arguments": {"path": str(tmp_folder / "next.md"), "content": "next"}}},
        ]}})
        continued = client.post("/chat/stream", json={
            "message": "Continue", "continuation_of": proposal["proposal_id"], "resume_workflow": True})
        followup = next(data for name, data in _events(continued.text) if name == "proposal")
        assert [step["args"]["path"] for step in followup["steps"]] == [str(tmp_folder / "next.md")]
        assert not (tmp_folder / "next.md").exists()
        assert client.post("/chat/stream", json={
            "message": "Continue again", "continuation_of": proposal["proposal_id"], "resume_workflow": True
        }).status_code == 409
        assert client.post("/chat/stream", json={"message": "Continue", "session_id": "unrelated-session",
                                                   "continuation_of": proposal["proposal_id"],
                                                   "resume_workflow": True}).status_code == 404
        assert client.post(f"/agent/proposals/{followup['proposal_id']}/approve").json()["status"] == "executed"
        assert client.get(history_url).json()["workflows"][1]["has_followup"]

        async def final_stream(messages, *args, **kwargs):
            assert proposal["proposal_id"][:8] in messages[0]["content"]
            assert followup["proposal_id"][:8] in messages[0]["content"]
            yield {"message": {"content": "Reviewed all receipts."}, "done": True}

        attempted_models = []

        def fallback_plan(messages, model, **kwargs):
            attempted_models.append(model)
            if model == "rejected-local":
                raise orchestrator.ollama.OllamaError("local model does not support tools")
            return {"message": {"tool_calls": [
                {"function": {"name": "fs_write", "arguments": {"path": str(tmp_folder / name), "content": content}}}
                for name, content in (("copy.md", "alpha beta"), ("next.md", "next"), ("third.md", "third"))
            ]}}

        monkeypatch.setattr(chat_router.ollama, "chat_stream", final_stream)
        monkeypatch.setattr(orchestrator, "_planner_models", lambda *args: ["rejected-local", "llama3.2:3b"])
        monkeypatch.setattr(orchestrator.ollama, "chat", fallback_plan)
        resumed = client.post("/chat/stream", json={
            "message": "Continue from the latest checkpoint", "continuation_of": followup["proposal_id"], "resume_workflow": True})
        events = _events(resumed.text)
        assert attempted_models == ["rejected-local", "llama3.2:3b"]
        assert any(event == "status" and data["message"] == "planner_fallback" for event, data in events)
        third = next(data for event, data in events if event == "proposal")
        assert [step["args"]["path"] for step in third["steps"]] == [str(tmp_folder / "third.md")]
    finally:
        grants.revoke_grant(g["id"])


def test_agent_endpoints(client):
    t = client.get("/agent/tools").json()["tools"]
    names = {x["name"] for x in t}
    assert {"fs_read", "fs_write", "run_python", "memory_search", "list_capabilities"} <= names
    assert next(x for x in t if x["name"] == "fs_write")["available"] is False
    run = client.post("/agent/tools/run", json={"tool": "list_capabilities", "args": {}}).json()
    assert run["receipt"]["outcome"] == "succeeded"
    assert client.get("/agent/proposals").status_code == 200
    assert client.post("/agent/proposals/nope/approve").status_code == 404
    assert client.get("/agent/receipts").json()["receipts"]


def test_proposal_only_runs_once_when_two_approvals_overlap(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from uuid import uuid4

    from poda_app.agent import orchestrator
    from poda_app.runtime.db import connect, now_iso

    proposal_id = str(uuid4())
    reached, release = Event(), Event()
    calls = []
    plan = {"steps": [{"tool": "run_tests", "args": {"cwd": "/synthetic"}}], "allow_repair": True}
    with connect() as db:
        db.execute("INSERT INTO agent_proposals VALUES (?,?,?,?,?,?,?)",
                   (proposal_id, None, now_iso(), "pending", json.dumps(plan), "[]", None))

    def execute(tool, *args, **kwargs):
        calls.append(tool)
        reached.set()
        assert release.wait(3)
        return {"receipt": {"id": "example-receipt", "outcome": "failed"},
                "result": {"ok": False, "error": "synthetic failure"}}

    monkeypatch.setattr(orchestrator.tools, "execute", execute)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(orchestrator.approve, proposal_id)
            try:
                assert reached.wait(3)
                assert orchestrator.approve(proposal_id)["status"] == "running"
                assert orchestrator.reject(proposal_id)["status"] == "running"
            finally:
                release.set()
            result = first.result(timeout=3)
            assert result["status"] == "failed"
            assert result["proposal"]["receipt_ids"] == ["example-receipt"]
            assert calls == ["run_tests"]
    finally:
        with connect() as db:
            db.execute("DELETE FROM agent_proposals WHERE id=?", (proposal_id,))
