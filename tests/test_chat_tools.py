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
        assert proposal["steps"][0]["tool"] == "fs_write" and not (tmp_folder / "copy.md").exists()
        approved = client.post(f"/agent/proposals/{proposal['proposal_id']}/approve").json()
        assert approved["status"] == "executed" and (tmp_folder / "copy.md").read_text() == "alpha beta"

        async def report_stream(messages, model, options=None, tools=None, think=None, timeout=900):
            assert "ACTION RECEIPTS" in messages[0]["content"] and "fs_write" in messages[0]["content"]
            yield {"message": {"content": "Done per receipt."}, "done": True}
        monkeypatch.setattr(chat_router.ollama, "chat_stream", report_stream)
        r2 = client.post("/chat/stream", json={"message": "Report what you did", "continuation_of": proposal["proposal_id"]})
        assert any(e == "done" for e, _ in _events(r2.text))
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
