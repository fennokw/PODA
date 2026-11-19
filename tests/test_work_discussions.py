import json
from contextlib import closing
from uuid import uuid4

from poda_app.chat import commands, discussions, learning
from poda_app.runtime.db import connect, rows
from poda_app.memory import store


def _event(response, name):
    for event in response.text.split("\n\n"):
        if event.startswith(f"event: {name}\n"):
            return json.loads(event.split("data: ", 1)[1])
    raise AssertionError(response.text)


def _model(monkeypatch, answer):
    from poda_app.chat import router as chat
    monkeypatch.setattr(chat.models, "resolve", lambda *a, **kw: {
        "model": "test", "profile": "fast", "num_ctx": 8192, "options": {},
    })
    monkeypatch.setattr(chat.orchestrator, "tool_prerequisites_exist", lambda _: False)

    async def stream(*args, **kwargs):
        yield {"message": {"content": answer}, "done": True}
    monkeypatch.setattr(chat.inference, "chat_stream", stream)


def test_commands_keep_mode_independent_of_storage():
    assert commands.parse("/work /teach me Solve Q2").mode == "teach"
    assert commands.parse("/work /teach me Solve Q2").message == "Solve Q2"
    assert commands.parse("/work /teach me Solve Q2").work
    assert not commands.parse("/learn Solve Q2").work
    assert commands.parse("/workbench test").message == "/workbench test"
    assert commands.parse("Show /work in an example").mode == ""


def test_ordinary_learning_publishes_memory_then_promotes_active_chat(client, monkeypatch):
    quiz = {"question": "What is two plus two?", "choices": [
        {"label": "3", "feedback": "Add the second two."}, {"label": "4", "feedback": "Two pairs contain four."}], "correct": 1}
    _model(monkeypatch, "Try this.\n\n```poda-quiz\n" + json.dumps(quiz) + "\n```")
    first = client.post("/chat/stream", json={"message": "/learn Review two pairs", "use_memory": False})
    session = _event(first, "session")
    assert session["work_discussion"] is False
    sid = session["session_id"]
    assert _event(first, "done")["learning_widgets"] == [{"kind": "quiz", **quiz}]
    assert client.get(f"/chat/session/{sid}/messages").json()["messages"] == []
    assert not any(item["session_id"] == sid for item in client.get("/chat/sessions").json()["sessions"])
    with closing(connect()) as db:
        assert rows(db, "SELECT 1 FROM memory_nodes WHERE source_message_id=?", (session["user_message_id"],))
    opened = client.post("/chat/stream", json={"message": "/work", "session_id": sid, "use_memory": False})
    assert _event(opened, "session")["session_id"] == sid
    messages = client.get(f"/chat/session/{sid}/messages").json()["messages"]
    assert messages[1]["learning_widgets"][0]["question"] == quiz["question"]
    assert client.patch(f"/chat/session/{sid}/title", json={"title": "Pair exercise"}).is_success
    assert any(item["label"] == "Pair exercise" for item in client.get("/chat/sessions").json()["sessions"])


def test_expired_working_messages_use_second_brain():
    sid = str(uuid4())
    for i in range(8):
        store.save_message("user", f"Explain exercise {i}", session_id=sid,
                           provenance=discussions.ORDINARY, defer_memory=True)
        store.save_message("assistant", "The response " + str(i), session_id=sid,
                           provenance=discussions.ORDINARY, defer_memory=True)
    with closing(connect()) as db:
        assert len(rows(db, "SELECT 1 FROM messages WHERE session_id=?", (sid,))) == 12
        assert rows(db, "SELECT 1 FROM memory_nodes WHERE content LIKE ?", ("%Explain exercise 0%The response 0%",))
    discussions.archive(sid, clear=True)
    with closing(connect()) as db:
        assert rows(db, "SELECT 1 FROM messages WHERE session_id=?", (sid,)) == []
        assert rows(db, "SELECT 1 FROM memory_nodes WHERE content LIKE ?", ("%Explain exercise 7%The response 7%",))


def test_checklist_tracks_task_progress_and_can_reopen(client):
    p = client.post("/project-manager/projects", json={"name": "Learning " + str(uuid4())}).json()
    task = client.post("/workload", json={"title": "Problem set", "project": p["name"]}).json()
    opened = client.post(f"/project-manager/projects/{p['id']}/chats", json={"task_id": task["id"]}).json()
    url = f"/chat/session/{opened['session_id']}/checklist"
    state = client.put(url, json={"revision": 0, "items": ["1(a) Derive", "1(b) Plot"]}).json()
    assert state["progress"] == 0
    first, second = [item["id"] for item in state["items"]]
    state = client.put(url, json={"revision": 1, "item_id": first, "completed": True}).json()
    assert state["progress"] == 50
    assert client.put(url, json={"revision": 1, "item_id": second, "completed": True}).status_code == 409
    state = client.put(url, json={"revision": 2, "item_id": second, "completed": True}).json()
    assert state["progress"] == 100 and state["status"] == "done"
    state = client.put(url, json={"revision": 3, "item_id": first, "completed": False}).json()
    assert state["progress"] == 50 and state["status"] == "active"


def test_learning_rejects_invalid_widget():
    text, widgets = learning.extract("Try again.\n\n```poda-graph\n{\"title\":\"wrong\",\"rate\":-1}\n```")
    assert text == "Try again." and widgets == []


def test_restarted_work_run_exposes_checkpoint(client):
    from poda_app.chat import runs
    session = str(uuid4())
    discussions.open_work(session)
    run = runs.ChatRun("survives-process", 0, session_id=session, persistent=True, prompt="/build finish tests")
    runs.checkpoint(run)
    with closing(connect()) as db, db:
        runs.interrupt_checkpoints(db)
    restored = client.get(f"/chat/session/{session}/messages").json()["interrupted"]
    assert restored["prompt"] == "/build finish tests"
    assert client.get("/chat/runs/survives-process").json()["state"] == "interrupted"


def test_repository_map_respects_grant_and_skips_generated_files(client, tmp_folder):
    from poda_app.agent import grants, repository
    (tmp_folder / "main.py").write_text("def start():\n    return 1\n")
    (tmp_folder / "tests").mkdir()
    (tmp_folder / "tests/test_example.py").write_text("def test_example():\n    assert True\n")
    (tmp_folder / "node_modules").mkdir()
    (tmp_folder / "node_modules/internal.py").write_text("def generated():\n    return 2\n")
    outside = tmp_folder.parent / "secret.py"
    outside.write_text("def outside(): pass")
    (tmp_folder / "symlink.py").symlink_to(outside)
    grant = grants.add_grant(str(tmp_folder), "read")
    try:
        result = repository.map_workspace(str(tmp_folder), "test example")["result"]
        assert [e["path"] for e in result["files"]] == ["tests/test_example.py", "main.py"]
        assert result["files"][0]["symbols"][0]["name"] == "test_example"
    finally:
        grants.revoke_grant(grant["id"])


def test_github_publish_uses_atomic_tree_and_checks_readback(monkeypatch):
    from poda_app.agent import tools
    from poda_app.connectors.github import service as github
    repo = "example/project"
    commit, tree, new_tree, head = "a"*40, "b"*40, "c"*40, "d"*40
    calls = []

    def request(target, path="", method="GET", payload=None, **kw):
        assert target == repo
        calls.append((method, path, payload))
        match (method, path.split("?")[0]):
            case ("GET", "/commits/main"): return {"sha": commit, "commit": {"tree": {"sha": tree}}}
            case ("GET", p) if p == "/git/trees/" + tree:
                return {"tree": [{"path": "start.sh", "mode": "100755"}], "truncated": False}
            case ("POST", "/git/trees"):
                assert payload["tree"][0]["mode"] == "100755"
                return {"sha": new_tree}
            case ("POST", "/git/commits"):
                assert payload["parents"] == [commit] and payload["tree"] == new_tree
                return {"sha": head}
            case ("POST", "/git/refs"):
                assert payload == {"ref": "refs/heads/poda/test", "sha": head}
                return {}
            case ("GET", "/pulls"): return []
            case ("POST", "/pulls"):
                assert payload["draft"] is True
                return {"number": 42}
            case ("GET", "/pulls/42"):
                return {"head": {"sha": head}, "base": {"ref": "main"}, "draft": True,
                        "html_url": "https://github.com/example/project/pull/42"}
        raise AssertionError(calls)

    monkeypatch.setattr(github, "_request", request)
    args = tools.validate_args("github_publish_patch", {"repo": repo, "base": "main", "expected_base": commit,
              "branch": "poda/test", "title": "Fix startup", "changes": [{"path": "start.sh", "content": "echo hello\n"}]})
    result = github.publish(**args)
    assert result["result"]["commit"] == head and result["result"]["draft"]


def test_public_reader_rejects_local_or_mixed_addresses(monkeypatch):
    import pytest
    from poda_app.connectors import research

    def mocked(*args, **kwargs):
        return [(None, None, None, None, ("8.8.8.8", 443)), (None, None, None, None, ("127.0.0.1", 443))]
    monkeypatch.setattr(research.socket, "getaddrinfo", mocked)
    with pytest.raises(ValueError, match="public internet"):
        research._address("example.test")
    with pytest.raises(ValueError, match="public HTTPS"):
        research._download("https://user:password@example.test/")


def test_git_worktree_isolated_and_read_back(client, tmp_folder, monkeypatch):
    import subprocess
    from poda_app.agent import exec_tools, grants, repository

    root = tmp_folder / "project"
    root.mkdir()
    git = lambda *args: subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("init", "-q")
    (root / "app.py").write_text("print(1)\n")
    git("add", "app.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "Initial")
    sha = git("rev-parse", "HEAD")
    grant = grants.add_grant(str(tmp_folder), "edit", allow_execute=True)

    def run(cmd, cwd, timeout):
        proc = subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        return {"ok": proc.returncode == 0, "result": {"stdout": proc.stdout, "stderr": proc.stderr,
                "stdout_truncated": False}, "error": None}
    monkeypatch.setattr(exec_tools, "_run", run)
    try:
        target = tmp_folder / "chat-worktree"
        result = repository.isolated_worktree(str(root), str(target), sha)
        assert result["result"]["base_sha"] == sha
        assert repository.isolated_worktree(str(root), str(target), sha) == result
        (target / "app.py").write_text("print(2)\n")
        assert (root / "app.py").read_text() == "print(1)\n"
        assert "+print(2)" in repository.git_state(str(target))["result"]["diff"]
    finally:
        grants.revoke_grant(grant["id"])


def test_linked_canvas_sync_writes_completion_only(client, monkeypatch):
    from poda_app.connectors.notion import mapping, service
    from poda_app.project_manager import folders, schedule
    project = client.post("/project-manager/projects", json={"name": "Canvas " + str(uuid4())}).json()
    task = client.post("/workload", json={"title": "Imported homework", "project": project["name"]}).json()
    updates = []
    values = {"Kind": "Task", "Done": False, "Assignment": "91234"}
    page = {"id": "page-91234", "properties": values, "in_trash": False}

    class Notion:
        def update_page(self, page_id, body):
            updates.append((page_id, body))
            values["Done"] = body["properties"]["Done"]["checkbox"]

    monkeypatch.setattr(schedule.calendar, "binding", lambda: {"provider": "notion", "data_source_id": "owner"})
    monkeypatch.setattr(schedule, "_canvas_source", lambda **_: (Notion(), {"type": "Kind", "completed": "Done"}, "Assignment"))
    monkeypatch.setattr(service, "query_pages", lambda **kwargs: [page])
    monkeypatch.setattr(mapping, "page_in_source", lambda *a: page)
    monkeypatch.setattr(service, "normalize_page", lambda data: data)
    url = f"/project-manager/projects/{project['id']}/tasks/{task['id']}/canvas"
    assert client.put(url, json={"assignment_id": "91234"}).json()["page_id"] == page["id"]
    assert not updates
    folders.update_task(project["id"], task["id"], {"progress": 100})
    assert updates == [(page["id"], {"properties": {"Done": {"checkbox": True}}})]
    assert values["Assignment"] == "91234"
