import time

from poda_app.connectors.calendar import service as calendar
from poda_app.project_manager import schedule
from poda_app.runtime.db import get_setting, set_setting


def _post(client, path, data):
    response = client.post(path, json=data)
    assert response.status_code == 200, response.text
    return response.json()


def _patch(client, path, data):
    response = client.patch(path, json=data)
    assert response.status_code == 200, response.text
    return response.json()


def _chat(client, prompt, session=None):
    started = _post(client, "/chat/runs", {"message": prompt, "session_id": session})
    path = "/chat/runs/" + started["run_id"]
    for _ in range(100):
        result = client.get(path).json()
        if result["state"] != "running":
            break
        time.sleep(0.02)
    assert result["state"] == "completed", result
    sid = started["session_id"]
    messages = client.get(f"/chat/session/{sid}/messages").json()["messages"]
    return sid, messages[-1]["content"]


def test_chat_commands_scope_to_project_task_and_complete_goal(client):
    project = _post(client, "/project-manager/projects", {"name": "Flight Dynamics"})
    wrong = _post(client, "/project-manager/projects", {"name": "Other Project"})
    task = _post(client, "/workload", {"title": "Solve exercises", "project": project["name"]})
    path = f'/project-manager/projects/{project["id"]}'
    task_path = path + f'/tasks/{task["id"]}'
    try:
        sid = _post(client, "/chat/work", {})["session_id"]
        filing = client.put(f"/chat/session/{sid}/filing",
                            json={"project_id": project["id"], "task_id": task["id"]})
        assert filing.json()["folder"]["task_title"] == "Solve exercises"
        assert [chat["session_id"] for chat in client.get(path).json()["chats"]] == [sid]
        assert client.get(f'/project-manager/projects/{wrong["id"]}/tasks/{task["id"]}').status_code == 404
        updated = _patch(client, task_path, {"goal": "Exercises solved", "goal_target": 4, "user_minutes": 45})
        assert updated["estimate"]["minutes"] == 45
        _, reply = _chat(client, "/taskprogress 3", sid)
        assert "75%" in reply
        _, reply = _chat(client, "/taskprogress 4", sid)
        assert "complete" in reply
        completed = client.get(task_path).json()
        assert completed["status"] == "done"
        assert completed["progress"] == 100
        assert completed["tracking"]["goal_current"] == 4
        _patch(client, task_path, {"progress": 0})
        _, reply = _chat(client, "/taskdone", sid)
        assert "complete" in reply
        _patch(client, task_path, {"progress": 0})
        _, reply = _chat(client, "Check it off.", sid)
        assert "complete" in reply
        assert client.get(path).json()["tasks"][0]["progress"] == 100
    finally:
        client.delete("/workload/" + task["id"])
        client.delete(path)
        client.delete("/project-manager/projects/" + wrong["id"])


def test_existing_chat_can_move_from_project_to_task_folder(client):
    origin = _post(client, "/project-manager/projects", {"name": "Older sketches"})
    target = _post(client, "/project-manager/projects", {"name": "Prototype build"})
    task = _post(client, "/workload", {"title": "Check tolerances", "project": target["name"]})
    origin_path = f'/project-manager/projects/{origin["id"]}'
    target_path = f'/project-manager/projects/{target["id"]}'
    try:
        session = _post(client, "/chat/work", {})["session_id"]
        _patch(client, f"/chat/session/{session}/title", {"title": "Older sketches"})
        client.put(f"/chat/session/{session}/filing", json={"project_id": origin["id"]})
        _chat(client, "/teach", session)
        matches = client.get("/project-manager/chats", params={"query": "OLDER SKETCHES"}).json()["chats"]
        assert any(hit["session_id"] == session and hit["project_name"] == origin["name"] for hit in matches)

        moved = _post(client, target_path + "/chats", {"task_id": task["id"], "session_id": session})
        assert moved["session_id"] == session
        assert client.get(origin_path).json()["chats"] == []
        task_chats = client.get(target_path + "/tasks/" + task["id"]).json()["chats"]
        assert [chat["session_id"] for chat in task_chats] == [session]
        assert client.get(f"/chat/session/{session}/messages").json()["messages"][0]["content"] == "/teach"
        assert client.post(target_path + "/chats", json={"session_id": "missing"}).status_code == 404
    finally:
        client.delete("/workload/" + task["id"])
        client.delete(origin_path)
        client.delete(target_path)


def test_deadline_updates_calendar_task_and_inherits_existing_event_time(client):
    original = get_setting("calendar_provider_v1", "")
    calendar.activate("local")
    project = _post(client, "/project-manager/projects", {"name": "Deadline Demo"})
    task = _post(client, "/workload", {"title": "Prepare slides", "project": project["name"]})
    path = f'/project-manager/projects/{project["id"]}/tasks/{task["id"]}'
    start = "2027-04-02T15:00:00+00:00"
    later = "2027-04-02T16:00:00+00:00"
    event = calendar.create_calendar_event("Design review", start)["result"]

    def on_calendar():
        return client.get("/calendar/events?start=2027-04-01&end=2027-04-04").json()["events"]

    try:
        _patch(client, path, {"deadline": "2027-04-01"})
        assert [e["start"] for e in on_calendar() if e.get("project_task_id") == task["id"]] == ["2027-04-01"]
        linked = _patch(client, path, {"event_id": event["page_id"], "calendar_scope": calendar.binding()})
        assert linked["deadline"] == start
        assert calendar.update_calendar_event(event["page_id"], start=later, end="2027-04-02T17:00:00+00:00")["ok"]
        assert client.get(path).json()["deadline"] == later
        manual = _patch(client, path, {"deadline": "2027-04-03", "progress": 100})
        assert manual["tracking"]["event_id"] is None
        entries = on_calendar()
        assert next(e for e in entries if e["page_id"] == event["page_id"])["start"] == later
        matches = [e for e in entries if e.get("project_task_id") == task["id"]]
        assert len(matches) == 1 and matches[0]["done"] and matches[0]["start"] == "2027-04-03"
        _patch(client, path, {"deadline": None})
        assert all(e.get("project_task_id") != task["id"] for e in on_calendar())
    finally:
        calendar.archive_calendar_event(event["page_id"])
        client.delete("/workload/" + task["id"])
        client.delete("/project-manager/projects/" + project["id"])
        set_setting("calendar_provider_v1", original)


def test_notion_deadline_retry_reuses_native_task(client, monkeypatch):
    from poda_app.connectors.notion import mapping, client as notion_client, service as notion_service

    scope = {"provider": "notion", "data_source_id": "fake-source"}
    monkeypatch.setattr(calendar, "binding", lambda: scope)
    monkeypatch.setattr(calendar, "_require", lambda *a: scope)
    monkeypatch.setattr(mapping, "require_write", lambda *a: scope)
    schema = {
        "Name": {"type": "title"}, "Date": {"type": "date"},
        "Type": {"type": "select", "options": ["Event", "Task"]},
        "Completed": {"type": "checkbox"}, "External Event ID": {"type": "rich_text"},
    }
    names = {"title": "Name", "date": "Date", "type": "Type", "completed": "Completed",
             "external_ids": {"ics_event_id": "External Event ID"}}
    monkeypatch.setattr(mapping, "live_mapping", lambda *a: ({"mapping": names}, schema))
    pages = {}
    creates = []

    class FakeNotion:
        def create_page(self, data):
            creates.append(data)
            self.update_page("fake-page", data)
            raise RuntimeError("Connection lost after create.")

        def update_page(self, page_id, data):
            pages[page_id] = {"id": page_id, "parent": {"data_source_id": "fake-source"},
                              "properties": {name: {"type": schema[name]["type"], **prop}
                                             for name, prop in data["properties"].items()}}

        def page(self, page_id):
            return pages[page_id]

    monkeypatch.setattr(notion_client, "NotionClient", FakeNotion)
    monkeypatch.setattr(notion_service, "query_pages", lambda **kw: [
        notion_service.normalize_page(page) for page in pages.values()
        if notion_service.normalize_page(page)["properties"]["External Event ID"]
        == kw["filter_obj"]["rich_text"]["equals"]])
    project = _post(client, "/project-manager/projects", {"name": "Notion deadline test"})
    task = _post(client, "/workload", {"title": "Finish model", "project": project["name"]})
    path = f'/project-manager/projects/{project["id"]}/tasks/{task["id"]}'
    try:
        first = _patch(client, path, {"deadline": "2027-03-01"})
        assert "Connection lost" in first["tracking"]["calendar_error"]
        assert schedule.overlay("2027-03-01", "2027-03-01", scope)[0]["is_deadline"]
        retry = _post(client, path + "/retry-calendar", {})
        assert not retry["tracking"]["calendar_error"]
        assert len(creates) == 1
        updated = _patch(client, path, {"deadline": "2027-03-02", "progress": 100})
        page = notion_service.normalize_page(pages["fake-page"])["properties"]
        assert updated["tracking"]["calendar_page"] == "fake-page"
        assert page["Type"] == "Task" and page["Completed"] is True
        assert page["Date"]["start"] == "2027-03-02"
        assert "Commitment" not in page
        calendar_row = {"page_id": "fake-page"}
        assert schedule.overlay("2027-03-02", "2027-03-02", scope, [calendar_row]) == []
        assert calendar_row["project_task_id"] == task["id"]
        _patch(client, path, {"deadline": None})
        assert notion_service.normalize_page(pages["fake-page"])["properties"]["Date"] is None
    finally:
        client.delete("/workload/" + task["id"])
        client.delete("/project-manager/projects/" + project["id"])


def test_manual_filing_scopes_task_commands_and_titles(client):
    name = 'Prototype "A"'
    project = _post(client, "/project-manager/projects", {"name": name})
    other = _post(client, "/project-manager/projects", {"name": "Separate project"})
    task = _post(client, "/workload", {"title": "Review design", "project": name})
    path = f'/project-manager/projects/{project["id"]}'
    sid = _post(client, "/chat/work", {})["session_id"]
    endpoint = "/project-manager/chat-commands"
    filing = f"/chat/session/{sid}/filing"
    try:
        assert client.get(endpoint, params={"session_id": sid}).json() == {"task_name": None, "goal_target": None}
        wrong = client.put(filing, json={"project_id": other["id"], "task_id": task["id"]})
        assert wrong.status_code == 404
        assert client.put(filing, json={"project_id": project["id"], "task_id": task["id"]}).json()["folder"]["project_name"] == name
        _patch(client, path + "/tasks/" + task["id"], {"goal_target": 3})
        assert client.get(endpoint, params={"session_id": sid}).json() == {"task_name": "Review design", "goal_target": 3}
        _patch(client, f"/chat/session/{sid}/title", {"title": "Design review"})
        assert client.get(path).json()["chats"][0]["title"] == "Design review"
        _, reply = _chat(client, '/project "Separate project"', sid)
        assert "top of the chat" in reply
        assert client.get(f"/chat/session/{sid}/header").json()["folder"]["project_id"] == project["id"]
        assert client.put(filing, json={}).json()["folder"] is None
        assert client.get(f"/chat/session/{sid}/header").json()["title"] == "Design review"
    finally:
        client.delete("/workload/" + task["id"])
        client.delete(path)
        client.delete("/project-manager/projects/" + other["id"])
