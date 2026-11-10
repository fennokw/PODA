import json

import pytest

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
        monkeypatch.setattr(orchestrator.inference, "chat", lambda *a, **k: next(plans))
        monkeypatch.setattr(orchestrator.inference, "status", lambda: {"reachable": True, "models": ["llama3.2:3b"]})

        async def fake_stream(messages, model, options=None, tools=None, think=None, timeout=900):
            system = messages[0]["content"]
            assert "PENDING PROPOSAL" in system and "TOOL RESULTS" in system and "TOOLS (PODA executes" in system
            yield {"message": {"content": "I will copy notes.md once you approve."}, "done": False}
            yield {"message": {"content": ""}, "done": True, "eval_count": 5, "eval_duration": 1000}
        monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
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
        monkeypatch.setattr(chat_router.inference, "chat_stream", report_stream)
        r2 = client.post("/chat/stream", json={"message": "Report what you did", "continuation_of": proposal["proposal_id"]})
        assert any(e == "done" for e, _ in _events(r2.text))

        async def continue_stream(messages, *args, **kwargs):
            assert "PENDING PROPOSAL" in messages[0]["content"]
            assert "ACTION RECEIPTS" in messages[0]["content"]
            yield {"message": {"content": "Ready to continue."}, "done": True}

        monkeypatch.setattr(chat_router.inference, "chat_stream", continue_stream)
        monkeypatch.setattr(orchestrator.inference, "chat", lambda *a, **kw: {"message": {"tool_calls": [
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
                raise orchestrator.inference.InferenceError("local model does not support tools")
            return {"message": {"tool_calls": [
                {"function": {"name": "fs_write", "arguments": {"path": str(tmp_folder / name), "content": content}}}
                for name, content in (("copy.md", "alpha beta"), ("next.md", "next"), ("third.md", "third"))
            ]}}

        monkeypatch.setattr(chat_router.inference, "chat_stream", final_stream)
        monkeypatch.setattr(orchestrator, "_planner_models", lambda *args: ["rejected-local", "llama3.2:3b"])
        monkeypatch.setattr(orchestrator.inference, "chat", fallback_plan)
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


def test_chat_download_is_available_without_filesystem_grants(client, monkeypatch):
    from poda_app.chat import router as chat_router

    async def fake_stream(messages, *args, **kwargs):
        assert "chat attachment named poda-output.csv" in messages[0]["content"]
        yield {"message": {"content": "Here is the file.\n\n```csv\nname,count\nalpha,3\n```"}, "done": True}

    monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {}})
    monkeypatch.setattr(chat_router.orchestrator, "tool_prerequisites_exist", lambda _: False)
    assert not grants.list_grants()
    response = client.post("/chat/stream", json={"message": "Create a CSV with name and count", "use_memory": False})
    events = dict(_events(response.text))
    session_id = events["session"]["session_id"]
    download = events["done"]["download"]
    assert download["filename"] == "poda-output.csv"
    base = f"/chat/session/{session_id}/messages"
    assert client.get(base).json()["messages"][-1]["download"] == download
    file = client.get(f"{base}/{download['message_id']}/download")
    assert file.content == b"name,count\nalpha,3\n"
    assert "attachment;" in file.headers["Content-Disposition"]
    assert client.get(f"{base}/{events['session']['user_message_id']}/download").status_code == 404
    assert client.get(f"/chat/session/unrelated/messages/{download['message_id']}/download").status_code == 404


def test_sandbox_pdf_from_chat_persists_and_can_be_revised(client, monkeypatch):
    import io
    from poda_app.agent import sandbox_files, tools
    from poda_app.chat import router as chat_router

    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")

    async def fake_stream(messages, *args, **kwargs):
        assert "one fenced markdown code block" in messages[0]["content"]
        yield {"message": {"content": "```markdown\n# PODA Sandbox\nVersion one.\n```"}, "done": True}

    monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {}})
    monkeypatch.setattr(chat_router.orchestrator, "tool_prerequisites_exist", lambda _: False)
    assert not grants.list_grants()
    response = client.post("/chat/stream", json={"message": "Create a PDF named workspace-regression.pdf", "use_memory": False})
    events = dict(_events(response.text))
    name = events["done"]["download"]["sandbox_file"]
    assert name == "workspace-regression.pdf"
    url = f"/agent/sandbox/download/{name}"
    first = client.get(url)
    assert "Version one." in pypdf.PdfReader(io.BytesIO(first.content)).pages[0].extract_text()
    assert (sandbox_files.root() / name).read_bytes() == first.content
    read = tools.execute("sandbox_read", {"path": name})["result"]["result"]
    assert "Version one." in read["content"]
    updated = tools.execute("sandbox_write", {"path": name, "content": "# PODA Sandbox\nVersion two.",
                                              "expected_sha256": read["sha256"]}, approved=True)
    assert updated["receipt"]["outcome"] == "succeeded"
    assert "Version two." in pypdf.PdfReader(io.BytesIO(client.get(url).content)).pages[0].extract_text()
    assert client.get(f"/chat/session/{events['session']['session_id']}/messages").json()["messages"][-1]["download"]["sandbox_file"] == name


def test_sandbox_raw_upload_and_edit_stay_inside_workspace(client, tmp_path):
    from poda_app.agent import tools
    import base64

    name = "sandbox-binary-regression.blob"
    original = b"\0\xff\x10"
    uploaded = client.post("/agent/sandbox/upload", files={"file": (name, original)})
    assert uploaded.status_code == 200 and uploaded.json()["filename"] == name
    url = f"/agent/sandbox/download/{name}"
    assert client.get(url).content == original
    assert client.get(url, headers={"Origin": "https://other.test"}).status_code == 403
    old = tools.execute("sandbox_read", {"path": name})["result"]["result"]
    assert old["encoding"] == "base64" and base64.b64decode(old["content"]) == original
    new = b"\0\xff\x11"
    args = {"path": name, "content": base64.b64encode(new).decode(), "encoding": "base64"}
    assert not tools.execute("sandbox_write", args, approved=True)["result"]["ok"]
    assert tools.execute("sandbox_write", {**args, "expected_sha256": old["sha256"]}, approved=True)["result"]["ok"]
    assert client.get(url).content == new
    assert not tools.execute("sandbox_write", {**args, "expected_sha256": old["sha256"]}, approved=True)["result"]["ok"]
    outside = tmp_path / "outside"
    for path in (str(outside), "../outside", "CON.txt"):
        assert not tools.execute("sandbox_write", {**args, "path": path}, approved=True)["result"]["ok"]
    assert not outside.exists()
    assert client.post("/agent/sandbox/upload", files={"file": ("../escape", original)}).status_code == 400


def test_sandbox_exports_native_word_and_excel(client):
    import io
    from poda_app.agent import sandbox_files

    docx = pytest.importorskip("docx")
    openpyxl = pytest.importorskip("openpyxl")
    sandbox_files.save_content("poda-regression.docx", "# Notes\nRésumé")
    saved = client.get("/agent/sandbox/download/poda-regression.docx")
    assert [paragraph.text for paragraph in docx.Document(io.BytesIO(saved.content)).paragraphs] == ["Notes", "Résumé"]
    sandbox_files.save_content("poda-regression.xlsx", "name,value\nalpha,=1+1\n")
    saved = client.get("/agent/sandbox/download/poda-regression.xlsx")
    book = openpyxl.load_workbook(io.BytesIO(saved.content))
    assert book.active["B2"].data_type == "s" and book.active["B2"].value == "=1+1"


def test_chat_routes_revision_of_last_workspace_file_without_folder_grant(client, monkeypatch):
    from poda_app.agent import sandbox_files
    from poda_app.chat import router as chat_router
    from poda_app.memory import store

    filename = "workspace-edit-regression.txt"
    saved = sandbox_files.save_content(filename, "old text")
    session_id = "sandbox-edit-regression"
    store.save_message("assistant", "Here is the file.", session_id=session_id, metadata={"sandbox_file": filename})
    calls = []

    def plan(message, snapshot, context, *args, **kwargs):
        calls.append(context)
        assert "sandbox_write" in context and filename in context
        return {"executed": [], "steps": [], "flagged": []}

    async def fake_stream(messages, *args, **kwargs):
        yield {"message": {"content": "Workspace revision route reached."}, "done": True}

    monkeypatch.setattr(chat_router.orchestrator, "plan", plan)
    monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {}})
    assert not grants.list_grants()
    res = client.post("/chat/stream", json={"message": "Change the file to say new text.", "use_memory": False, "session_id": session_id})
    assert calls and dict(_events(res.text))["done"]["model"] == "test"
    assert sandbox_files.sandbox_read(filename)["result"]["sha256"] == saved["sha256"]


@pytest.mark.parametrize("retry", [False, True])
def test_mermaid_markdown_export_without_a_folder_grant(client, monkeypatch, retry):
    from poda_app.agent import sandbox_files
    from poda_app.chat import router as chat_router
    from poda_app.memory import store

    task = "Create a mermaid.js flow chart of the process to creating a startup and output it as a Markdown file."
    refusal = "The runtime ledger says I cannot create or edit files."
    chart = "graph LR\n    A[Define Problem] --> B[Research Solutions]"
    expected = f"```mermaid\n{chart}\n```\n"
    session_id = f"mermaid-markdown-{retry}"
    if retry:
        store.save_message("user", task, session_id=session_id, defer_memory=True)
        store.save_message("assistant", f"{refusal}\n\n{expected}", session_id=session_id, defer_memory=True)
        store.save_message("user", "Try again.", session_id=session_id, defer_memory=True)
        store.save_message("assistant", refusal, session_id=session_id, defer_memory=True)

    async def fake_stream(messages, *args, **kwargs):
        assert messages[-1]["content"] == task
        assert "poda-output.md" in messages[0]["content"] and "No user folder grant is needed" in messages[0]["content"]
        yield {"message": {"content": refusal + "\n\n"}, "done": False}
        yield {"message": {"content": expected + "\nCan you confirm the steps?"}, "done": True}

    monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {}})
    monkeypatch.setattr(chat_router.orchestrator, "tool_prerequisites_exist", lambda _: False)
    assert not grants.list_grants()
    response = client.post("/chat/stream", json={"message": "Now try." if retry else task,
                                                "session_id": session_id, "use_memory": False})
    events = _events(response.text)
    assert "".join(data["token"] for event, data in events if event == "token") == expected
    download = dict(events)["done"]["download"]
    assert download["sandbox_file"].endswith(".md") and download["error"] is None
    assert sandbox_files.read_bytes(download["sandbox_file"]).decode() == expected
    saved = client.get(f"/chat/session/{session_id}/messages").json()["messages"]
    assert saved[-1]["download"] == download
    assert client.get(f"/chat/session/{session_id}/messages/{download['message_id']}/download").text == expected


def test_mermaid_markdown_attachment_preserves_an_explicit_document_and_checks_scope():
    from poda_app.agent import gate
    from poda_app.chat import artifacts

    document = "# Startup\n\n```mermaid\nflowchart TD\nA --> B\n```\n"
    wrapped = f"````markdown\n{document}````"
    assert artifacts.generated_source(wrapped, "startup.md", mermaid=True) == (document, "auto")
    assert artifacts.generated_source("I can draft a diagram.", "startup.md", mermaid=True) is None
    assert artifacts.requested_name("Create a Mermaid.js startup flowchart") == "poda-output.md"
    assert artifacts.requested_name('Create a Mermaid.js diagram named "Startup process.md"') == "Startup process.md"
    assert artifacts.requested_name("Create a Mermaid flowchart named startup.mmd") == "startup.mmd"
    assert artifacts.generated_source(document, "startup.mmd", mermaid=True) == ("flowchart TD\nA --> B\n", "auto")
    outside = "Save the Mermaid.js chart as a Markdown file on my Desktop."
    assert artifacts.requested_name(outside) is None
    cap = {"filesystem": {"general_user_file_create": False}}
    assert "outside PODA Sandbox" in gate.unsupported_action_response(outside, cap)


def test_chat_creates_one_verified_zip_instead_of_separate_files(client, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile

    from poda_app.agent import sandbox_files
    from poda_app.chat import router as chat_router

    task = 'Make me a short python script named rainbow.py that says "hello world" in an animated rainbow graphic in a zip file export.'
    members = {"rainbow.py": "print('hello world')\n", "requirements.txt": "pygame\n"}
    known = {row["filename"] for row in sandbox_files.files()}
    calls = iter([{"message": {"tool_calls": [{"function": {"name": "sandbox_write", "arguments": {
        "path": "rainbow.py", "content": "ignored"}}}]}}, {"message": {"content": "Ready to package."}}])
    monkeypatch.setattr(chat_router.orchestrator, "_planner_models", lambda _: ["test"])
    monkeypatch.setattr(chat_router.inference, "chat", lambda *a, **k: next(calls))
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {}})

    async def fake_stream(messages, *args, **kwargs):
        assert "poda-output.zip" in messages[0]["content"] and "PODA builds one real ZIP" in messages[0]["content"]
        body = json.dumps([{"filename": name, "content": content} for name, content in members.items()])
        yield {"message": {"content": f"Would you like me to proceed?\n```json\n{body}\n```"}, "done": True}

    monkeypatch.setattr(chat_router.inference, "chat_stream", fake_stream)
    assert not grants.list_grants()
    response = client.post("/chat/stream", json={"message": task, "use_memory": False})
    events = _events(response.text)
    assert not any(event == "proposal" for event, _ in events)
    assert "Would you like" not in response.text
    download = dict(events)["done"]["download"]
    name = download["sandbox_file"]
    assert name == "poda-output.zip"
    assert {row["filename"] for row in sandbox_files.files()} - known == {name}
    with ZipFile(BytesIO(client.get(f"/agent/sandbox/download/{name}").content)) as zipped:
        assert zipped.testzip() is None
        assert {name: zipped.read(name).decode() for name in zipped.namelist()} == members
    assert sandbox_files.sandbox_write("bad.zip", '[{"filename":"../escape","content":"x"}]')["ok"] is False


def test_previous_zip_checkpoint_reports_receipts_and_packs_saved_bytes(client, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile

    from poda_app.agent import tools
    from poda_app.chat import router as chat_router
    from poda_app.memory import store
    from poda_app.runtime.db import connect, now_iso

    sid, proposal = "zip-checkpoint-session", "zip-checkpoint-proposal"
    filename, content = "zip-checkpoint.py", "print('verified')\n"
    task = "Create a short Python script in a ZIP file named checkpoint.zip."
    store.save_message("user", task, session_id=sid, defer_memory=True)
    store.save_message("assistant", "The script needs approval.", session_id=sid, defer_memory=True)
    run = tools.execute("sandbox_write", {"path": filename, "content": content}, approved=True, session_id=sid)
    plan = {"message": task, "steps": [{"tool": "sandbox_write", "args": {"path": filename, "content": content}}]}
    db = connect()
    try:
        db.execute("""INSERT INTO agent_proposals (id, session_id, created_at, status, plan_json, receipt_ids_json)
                      VALUES (?, ?, ?, 'executed', ?, ?)""",
                   (proposal, sid, now_iso(), json.dumps(plan), json.dumps([run["receipt"]["id"]])))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(chat_router.models, "resolve", lambda *a, **k: {})
    report = client.post("/chat/stream", json={"message": "Report the outcome using the receipts.", "continuation_of": proposal})
    assert "Saved and verified" in report.text and "Continue original workflow" in report.text
    resumed = client.post("/chat/stream", json={"message": "Continue the original workflow.", "continuation_of": proposal,
                                              "resume_workflow": True})
    download = dict(_events(resumed.text))["done"]["download"]
    assert download["sandbox_file"] == "checkpoint.zip"
    with ZipFile(BytesIO(client.get("/agent/sandbox/download/checkpoint.zip").content)) as zipped:
        assert zipped.namelist() == [filename] and zipped.read(filename).decode() == content


def test_storage_folder_opener_uses_active_profile_only(client, monkeypatch):
    from poda_app.runtime import config, router as runtime_router

    opened = []
    if config.WINDOWS:
        monkeypatch.setattr(runtime_router.os, "startfile", lambda path: opened.append(path))
    else:
        monkeypatch.setattr(runtime_router.shutil, "which", lambda _: "/usr/bin/xdg-open")
        monkeypatch.setattr(runtime_router.subprocess, "run", lambda args, **kwargs: opened.append(args[1]))
    storage = client.get("/system/storage").json()
    assert storage["data_directory"] == str(config.DATA_DIR)
    assert storage["database"] == str(config.DB_PATH)
    assert client.post("/system/storage/open", headers={"Origin": "https://other.test"}).status_code == 403
    response = client.post("/system/storage/open", json={"path": "/unrelated"})
    assert response.status_code == 200 and opened == [str(config.DATA_DIR)]
