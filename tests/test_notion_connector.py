"""Mocked-HTTP tests for the Notion connector. No live Notion access exists on this Mac."""
from __future__ import annotations

import json

import pytest

from poda_app.connectors.notion import client as nclient, diagnostics, discovery, mapping, service, state
from poda_app.connectors.notion.client import NotionError
from poda_app.runtime.db import connect
from poda_app.security import keychain

DB = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
VIEW = "9f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
DS = "11111111-2222-3333-4444-555555555555"
DS2 = "11111111-2222-3333-4444-666666666666"
REL_OK = "22222222-2222-3333-4444-555555555555"
REL_BAD = "33333333-2222-3333-4444-555555555555"
GOOD_DB = "44444444-2222-3333-4444-555555555555"


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body


def rt(text):
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def nf(msg="Could not find object"):
    return FakeResponse(404, {"object": "error", "status": 404, "code": "object_not_found", "message": msg})


SCHEMA = {
    "Name": {"id": "title", "type": "title", "title": {}},
    "Due Date / Event Date": {"id": "d1", "type": "date", "date": {}},
    "Status": {"id": "s1", "type": "status", "status": {"options": [{"name": "Not started"}, {"name": "Done"}], "groups": []}},
    "Type": {"id": "t1", "type": "select", "select": {"options": [{"name": "Assignment"}, {"name": "Event"}]}},
    "Project Domain": {"id": "r1", "type": "relation", "relation": {"data_source_id": REL_OK}},
    "Class": {"id": "r2", "type": "relation", "relation": {"data_source_id": REL_BAD}},
    "Canvas Assignment ID": {"id": "c1", "type": "rich_text", "rich_text": {}},
    "ICS Event ID": {"id": "c2", "type": "rich_text", "rich_text": {}},
}


def make_page(pid, title, start, end=None, status="Not started", canvas=None, in_trash=False, edited="2026-09-30T10:00:00.000Z"):
    date = {"start": start, "end": end, "time_zone": None} if start else None
    return {"object": "page", "id": pid, "url": f"https://notion.so/{pid}", "last_edited_time": edited, "in_trash": in_trash,
            "parent": {"type": "data_source_id", "data_source_id": DS},
            "properties": {"Name": {"type": "title", "title": rt(title)}, "Due Date / Event Date": {"type": "date", "date": date},
                           "Status": {"type": "status", "status": {"name": status}}, "Type": {"type": "select", "select": {"name": "Assignment"}},
                           "Project Domain": {"type": "relation", "relation": [{"id": "p-1"}]}, "Class": {"type": "relation", "relation": []},
                           "Canvas Assignment ID": {"type": "rich_text", "rich_text": rt(canvas) if canvas else []}, "ICS Event ID": {"type": "rich_text", "rich_text": []}}}


class FakeNotion:
    """Route table keyed by (method, path). Values are FakeResponse or callables(payload)->FakeResponse."""

    def __init__(self):
        self.routes = {}
        self.calls = []
        self.token_valid = True
        self.pages = {}
        self.created = []
        self.hook = None

    def install_workspace(self, with_db=True, page_count=3, extra_sources=None):
        self.routes[("GET", "/users/me")] = FakeResponse(200, {"object": "user", "id": "bot-1", "name": "PODA", "type": "bot",
                                                              "bot": {"owner": {"type": "workspace", "workspace": True}, "workspace_id": "ws-1", "workspace_name": "Example user HQ"}})
        if with_db:
            self.routes[("GET", f"/databases/{GOOD_DB}")] = FakeResponse(200, {"object": "database", "id": GOOD_DB, "title": rt("To Do List"), "data_sources": [{"id": DS, "name": "To Do List"}] + (extra_sources or []), "parent": {"type": "page_id", "page_id": "pg"}})
            self.routes[("GET", f"/data_sources/{DS}")] = FakeResponse(200, {"object": "data_source", "id": DS, "title": rt("To Do List"), "properties": SCHEMA, "parent": {"type": "database_id", "database_id": GOOD_DB}})
            self.routes[("GET", f"/data_sources/{REL_OK}")] = FakeResponse(200, {"object": "data_source", "id": REL_OK, "title": rt("Project Domain"), "properties": {}, "parent": {"type": "database_id", "database_id": "x"}})
            self.routes[("GET", f"/data_sources/{REL_BAD}")] = nf()
            for i in range(page_count):
                pid = f"page-{i}"
                self.pages[pid] = make_page(pid, f"Task {i}", f"2026-10-0{i+1}", canvas=f"canvas-{i}" if i == 0 else None)
            self.routes[("POST", f"/data_sources/{DS}/query")] = self.query
            self.routes[("POST", "/search")] = FakeResponse(200, {"object": "list", "results": [{"object": "data_source", "id": DS, "title": rt("To Do List"), "parent": {"type": "database_id", "database_id": GOOD_DB}, "url": "u", "last_edited_time": "t"}],
                                                                   "has_more": False, "next_cursor": None, "request_status": {"type": "complete"}})
            self.routes[("POST", "/pages")] = self.create_page
        else:
            self.routes[("POST", "/search")] = FakeResponse(200, {"object": "list", "results": [], "has_more": False, "next_cursor": None, "request_status": {"type": "complete"}})
        return self

    def query(self, payload):
        items = [p for p in self.pages.values()]
        size = payload.get("page_size", 100)
        start = int(payload.get("start_cursor") or 0)
        batch = items[start:start + size]
        more = start + size < len(items)
        return FakeResponse(200, {"object": "list", "results": batch, "has_more": more, "next_cursor": str(start + size) if more else None})

    def create_page(self, payload):
        pid = f"created-{len(self.created)+1}"
        title = payload["properties"]["Name"]["title"][0]["text"]["content"]
        date = (payload["properties"].get("Due Date / Event Date") or {}).get("date") or {}
        page = make_page(pid, title, date.get("start"), date.get("end"))
        self.pages[pid] = page
        self.created.append(pid)
        return FakeResponse(200, page)

    def __call__(self, service, method, url, *, headers=None, json_body=None, timeout=25, purpose=None):
        assert service == "notion"
        assert headers["Authorization"].startswith("Bearer ")
        path = url.split("/v1", 1)[1]
        self.calls.append((method, path, json_body))
        if self.hook is not None:
            hooked = self.hook(method, path, json_body)
            if hooked is not None:
                return hooked
        if not self.token_valid:
            return FakeResponse(401, {"object": "error", "status": 401, "code": "unauthorized", "message": "API token is invalid."})
        if method == "GET" and path.startswith("/pages/"):
            pid = path.split("/pages/")[1]
            return FakeResponse(200, self.pages[pid]) if pid in self.pages else nf()
        if method == "PATCH" and path.startswith("/pages/"):
            pid = path.split("/pages/")[1]
            if ("PATCH", path) in self.routes:
                return self.routes[("PATCH", path)]
            if pid not in self.pages:
                return nf()
            if json_body.get("in_trash"):
                self.pages[pid]["in_trash"] = True
            for name, val in (json_body.get("properties") or {}).items():
                self.pages[pid]["properties"][name] = {"type": list(val)[0], **val}
            self.pages[pid]["last_edited_time"] = "2026-10-01T12:00:00.000Z"
            return FakeResponse(200, self.pages[pid])
        handler = self.routes.get((method, path))
        if handler is None:
            return nf()
        return handler(json_body) if callable(handler) else handler


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeNotion()
    store = {"token": "ntn_test_token_value_1234567890"}
    monkeypatch.setattr("poda_app.security.egress.guarded_request", fake)
    monkeypatch.setattr(keychain, "keychain_get", lambda s, a: store.get("token") if s == "PODA.Public.Notion" else None)
    monkeypatch.setattr(keychain, "keychain_exists", lambda s, a: bool(store.get("token")) if s == "PODA.Public.Notion" else False)
    monkeypatch.setattr(keychain, "keychain_set", lambda s, a, v: store.__setitem__("token", v))
    monkeypatch.setattr(keychain, "keychain_delete", lambda s, a: store.pop("token", None) is not None)
    monkeypatch.setattr(nclient, "_sleep", lambda s: None)
    monkeypatch.setattr("poda_app.security.egress.offline_mode", lambda: False)
    fake.store = store
    # start clean (schema may not exist yet if the app lifespan has not run)
    from poda_app.runtime.db import init_database
    init_database()
    conn = connect()
    conn.execute("DELETE FROM notion_config"); conn.execute("DELETE FROM notion_mappings"); conn.execute("DELETE FROM agent_proposals")
    conn.execute("CREATE TABLE IF NOT EXISTS notion_test_pages (page_id TEXT PRIMARY KEY, data_source_id TEXT, title TEXT, created_at TEXT NOT NULL, archived INTEGER DEFAULT 0, note TEXT)")
    conn.execute("DELETE FROM notion_test_pages"); conn.commit(); conn.close()
    yield fake


def connect_ok(fake):
    fake.install_workspace()
    return service.connect_data_source(DS)


# ----------------------------------------------------------------------------- token / discovery
def test_validate_token_stores_only_after_success(fake):
    fake.install_workspace()
    fake.store.clear()
    out = discovery.validate_token("ntn_new_token_abcdefghij")
    assert out["valid"] and out["workspace_name"] == "Example user HQ" and "token" not in json.dumps(out).lower().replace("token_storage", "")
    assert fake.store["token"] == "ntn_new_token_abcdefghij"


def test_invalid_token_is_classified(fake):
    fake.install_workspace(); fake.token_valid = False
    fake.store.clear()
    with pytest.raises(NotionError) as exc:
        discovery.validate_token("ntn_bad")
    assert exc.value.kind == "invalid_token" and not fake.store


def test_discover_lists_data_sources_with_database_titles(fake):
    fake.install_workspace()
    out = discovery.list_data_sources()
    assert out["data_sources"][0]["id"] == DS and out["data_sources"][0]["database_title"] == "To Do List" and out["has_more"] is False


def test_rate_limit_retries_then_succeeds(fake):
    fake.install_workspace()
    hits = {"n": 0}
    def flaky(payload):
        hits["n"] += 1
        return FakeResponse(429, {"code": "rate_limited", "message": "slow down"}, {"Retry-After": "0"}) if hits["n"] == 1 else FakeResponse(200, {"results": [], "has_more": False, "request_status": {"type": "complete"}})
    fake.routes[("POST", "/search")] = flaky
    assert discovery.list_data_sources()["data_sources"] == [] and hits["n"] == 2


# ----------------------------------------------------------------------------- diagnostics
def test_exact_404_id_is_probed_not_assumed(fake):
    fake.install_workspace()
    out = diagnostics.diagnose(f"https://www.notion.so/ws/To-Do-{DB.replace('-', '')}?v={VIEW.replace('-', '')}")
    probed = {p for m, p, _ in fake.calls}
    assert f"/databases/{DB}" in probed and f"/data_sources/{DB}" in probed and f"/pages/{DB}" in probed
    codes = {d["code"] for d in out["diagnosis"]}
    assert "WRONG_WORKSPACE_OR_UNSHARED" in codes and "AMBIGUOUS_404" in codes and "VIEW_ID_NOT_DATABASE" in codes
    assert out["resolved"] is None
    assert any("Connections → Add connections" in (d.get("remedy") or "") for d in out["diagnosis"])
    amb = next(d for d in out["diagnosis"] if d["code"] == "AMBIGUOUS_404")
    assert len(amb["possibilities"]) >= 4


def test_unshared_with_nothing_visible(fake):
    fake.install_workspace(with_db=False)
    out = diagnostics.diagnose(DB)
    codes = {d["code"] for d in out["diagnosis"]}
    assert "AMBIGUOUS_404" in codes and "NOTHING_SHARED" in codes and "WRONG_WORKSPACE_OR_UNSHARED" not in codes


def test_invalid_token_diagnosis(fake):
    fake.install_workspace(); fake.token_valid = False
    out = diagnostics.diagnose(DB)
    assert out["diagnosis"][0]["code"] == "INVALID_TOKEN"


def test_resolves_database_and_flags_unshared_relation(fake):
    fake.install_workspace()
    out = diagnostics.diagnose(f"https://www.notion.so/ws/{GOOD_DB.replace('-', '')}?v={VIEW.replace('-', '')}")
    codes = [d["code"] for d in out["diagnosis"]]
    assert "DATA_SOURCE_RESOLVED" in codes and "RELATED_DATABASE_UNSHARED" in codes
    assert out["resolved"]["data_source_id"] == DS
    rel = next(d for d in out["diagnosis"] if d["code"] == "RELATED_DATABASE_UNSHARED")
    assert rel["property"] == "Class"


def test_multiple_data_sources_requires_choice(fake):
    fake.install_workspace(extra_sources=[{"id": DS2, "name": "Archive"}])
    out = diagnostics.diagnose(GOOD_DB)
    assert any(d["code"] == "MULTIPLE_DATA_SOURCES" for d in out["diagnosis"]) and out["resolved"]["data_source_id"] is None


def test_page_id_is_diagnosed_as_page(fake):
    fake.install_workspace()
    out = diagnostics.diagnose(f"https://www.notion.so/ws/page-0?v={VIEW.replace('-', '')}".replace("page-0", "page-0"))
    # bare 'page-0' is not a 32-hex id; use a real id mapped to a page instead
    fake.pages["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"] = make_page("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Dashboard", None)
    out = diagnostics.diagnose(f"https://www.notion.so/ws/aaaaaaaabbbbccccddddeeeeeeeeeeee?v={VIEW.replace('-', '')}")
    codes = {d["code"] for d in out["diagnosis"]}
    assert "PAGE_NOT_DATABASE" in codes and "LINKED_VIEW_SUSPECTED" in codes


def test_offline_mode_short_circuits(fake, monkeypatch):
    monkeypatch.setattr("poda_app.security.egress.offline_mode", lambda: True)
    out = diagnostics.diagnose(DB)
    assert out["diagnosis"][0]["code"] == "OFFLINE_MODE" and fake.calls == []


# ----------------------------------------------------------------------------- connect / status / write
def test_connect_persists_verified_ids_and_degrades_on_unshared_relation(fake):
    out = connect_ok(fake)
    assert out["connected"] and out["status"] == "DEGRADED" and out["workspace_id"] == "ws-1" and out["read_verified"]
    cap = state.capability()
    assert cap["connected"] and cap["can_read_content"] and not cap["can_insert_content"] and cap["schema_fingerprint"]
    assert "token" not in json.dumps(state.get_config()).lower().replace("token_storage", "")


def test_connect_rejects_data_source_not_in_database(fake):
    fake.install_workspace()
    fake.routes[("GET", f"/data_sources/{DS}")] = FakeResponse(200, {"object": "data_source", "id": DS, "title": rt("x"), "properties": SCHEMA, "parent": {"type": "database_id", "database_id": GOOD_DB}})
    fake.routes[("GET", f"/databases/{GOOD_DB}")] = FakeResponse(200, {"object": "database", "id": GOOD_DB, "title": rt("Other"), "data_sources": [{"id": DS2, "name": "Other"}]})
    with pytest.raises(NotionError):
        service.connect_data_source(DS)


def test_status_verify_detects_schema_change_and_revocation(fake):
    connect_ok(fake)
    changed = dict(SCHEMA); changed["Extra"] = {"id": "e1", "type": "checkbox", "checkbox": {}}
    fake.routes[("GET", f"/data_sources/{DS}")] = FakeResponse(200, {"object": "data_source", "id": DS, "title": rt("To Do List"), "properties": changed, "parent": {"type": "database_id", "database_id": GOOD_DB}})
    out = service.status(verify=True)
    assert out["status"] == "DEGRADED" and out["schema_changed"] is True
    fake.routes[("GET", f"/data_sources/{DS}")] = nf()
    out = service.status(verify=True)
    assert out["status"] == "DISCONNECTED" and not state.capability()["connected"]


def test_query_paginates_over_two_pages(fake):
    connect_ok(fake)
    fake.pages = {f"p{i}": make_page(f"p{i}", f"T{i}", "2026-10-01") for i in range(150)}
    pages = service.query_pages(limit=150)
    assert len(pages) == 150 and sum(1 for m, p, _ in fake.calls if p.endswith("/query")) >= 3  # 1 at connect + 2 pages


def test_verify_write_success_records_and_archives(fake):
    connect_ok(fake)
    out = service.verify_write()
    assert out["write_verified"] and out["insert_verified"] and out["update_verified"] and out["test_page_archived"] and out["leftover_test_pages"] == []
    cfg = state.get_config()
    assert cfg["insert_verified"] == 1 and cfg["update_verified"] == 1
    assert service.all_test_pages()[0]["archived"] == 1


def test_verify_write_archive_failure_leaves_truthful_leftover(fake):
    connect_ok(fake)
    fake.hook = lambda method, path, body: FakeResponse(403, {"code": "restricted_resource", "message": "Insufficient permissions for this endpoint."}) if (method == "PATCH" and "/pages/created-" in path) else None
    with pytest.raises(NotionError) as exc:
        service.verify_write()
    assert "may remain in Notion" in exc.value.message
    cfg = state.get_config()
    assert cfg["insert_verified"] == 1 and cfg["update_verified"] == 0
    leftovers = service.leftover_test_pages()
    assert len(leftovers) == 1 and leftovers[0]["page_id"] == "created-1"
    assert state.capability()["can_insert_content"] and not state.capability()["can_update_content"]


def test_disconnect_drops_capability_immediately(fake):
    connect_ok(fake)
    assert state.capability()["connected"]
    out = service.disconnect()
    assert out["status"] == "DISCONNECTED" and out["token_deleted"]
    assert state.capability() == state.capability() and not state.capability()["connected"] and not state.token_present()


# ----------------------------------------------------------------------------- mapping
GOOD_MAPPING = {"title": "Name", "date": "Due Date / Event Date", "status": "Status", "type": "Type", "project_relation": "Project Domain", "class_relation": "Class",
                "external_ids": {"canvas_assignment_id": "Canvas Assignment ID", "ics_event_id": "ICS Event ID"}, "status_done_values": ["Done"]}


def test_mapping_rejects_wrong_types_and_missing(fake):
    connect_ok(fake)
    with pytest.raises(NotionError) as exc:
        mapping.save_mapping({"title": "Status", "date": "Name"})
    assert "expected one of" in exc.value.message
    with pytest.raises(NotionError):
        mapping.save_mapping({"title": "Name", "date": "Nope"})
    with pytest.raises(NotionError):
        mapping.save_mapping({"title": "Name"})


def test_mapping_versions_and_preview_interprets_dates(fake):
    connect_ok(fake)
    v1 = mapping.save_mapping(GOOD_MAPPING)
    v2 = mapping.save_mapping(GOOD_MAPPING)
    assert v1["version"] == 1 and v2["version"] == 2 and mapping.get_active_mapping()["version"] == 2
    fake.pages["timed"] = make_page("timed", "Lecture", "2026-10-03T14:00:00.000-07:00", "2026-10-03T15:00:00.000-07:00", status="Done")
    out = mapping.preview(5)
    by_title = {p["interpreted"]["title"]: p["interpreted"] for p in out["pages"]}
    assert by_title["Task 0"]["all_day"] is True and by_title["Task 0"]["external_ids"]["canvas_assignment_id"] == "canvas-0"
    assert by_title["Lecture"]["all_day"] is False and by_title["Lecture"]["date_end"].startswith("2026-10-03T15") and by_title["Lecture"]["done"] is True


def test_propose_detects_duplicates_and_commit_rereads(fake):
    connect_ok(fake); service.verify_write(); mapping.save_mapping(GOOD_MAPPING)
    dup = mapping.propose_event({"title": "task 0", "start": "2026-10-01", "external_ids": {"canvas_assignment_id": "canvas-0"}})
    assert dup["duplicates"] and {"same canvas_assignment_id", "same title and date"} <= set(dup["duplicates"][0]["reasons"])
    prop = mapping.propose_event({"title": "Dentist", "start": "2026-10-09T14:00:00-07:00", "end": "2026-10-09T15:00:00-07:00", "time_zone": "America/Los_Angeles", "status": "Not started"})
    assert prop["requires_confirmation"] and prop["duplicates"] == []
    assert prop["payload_preview"]["properties"]["Due Date / Event Date"]["date"]["time_zone"] == "America/Los_Angeles"
    out = mapping.commit_event(prop["proposal_id"])
    assert out["created"] and out["verified"] and out["receipt"]["outcome"] == "success" and out["receipt"]["verification"] == "re-read-after-write"
    again = mapping.propose_event({"title": "Dentist", "start": "2026-10-09T14:00:00-07:00", "end": "2026-10-09T15:00:00-07:00", "time_zone": "America/Los_Angeles", "status": "Not started"})
    assert any("idempotency" in r for d in again["duplicates"] for r in d["reasons"])
    with pytest.raises(NotionError):
        mapping.commit_event(prop["proposal_id"])  # already committed


def test_commit_never_succeeds_on_api_failure(fake):
    connect_ok(fake); service.verify_write(); mapping.save_mapping(GOOD_MAPPING)
    prop = mapping.propose_event({"title": "Will fail", "start": "2026-11-01"})
    fake.routes[("POST", "/pages")] = FakeResponse(500, {"code": "internal_server_error", "message": "boom"})
    with pytest.raises(NotionError):
        mapping.commit_event(prop["proposal_id"])
    from poda_app.agent.receipts import list_receipts
    r = next(r for r in list_receipts(20) if r["tool"] == "notion.create_event" and "Will fail" in (r["args_json"] or ""))
    assert r["outcome"] == "failed" and "boom" in (r["error"] or "")
    conn = connect(); status_val = conn.execute("SELECT status FROM agent_proposals WHERE id=?", (prop["proposal_id"],)).fetchone()[0]; conn.close()
    assert status_val == "failed"


def test_commit_blocked_without_write_verification(fake):
    connect_ok(fake); mapping.save_mapping(GOOD_MAPPING)
    prop = mapping.propose_event({"title": "Blocked", "start": "2026-11-01"})
    with pytest.raises(NotionError) as exc:
        mapping.commit_event(prop["proposal_id"])
    assert exc.value.code == "write_unverified"


def test_update_conflict_and_archive_confirmation(fake):
    connect_ok(fake); service.verify_write(); mapping.save_mapping(GOOD_MAPPING)
    with pytest.raises(NotionError) as exc:
        mapping.update_event("page-1", {"title": "Renamed"}, expected_last_edited_time="2000-01-01T00:00:00.000Z")
    assert exc.value.code == "conflict"
    out = mapping.update_event("page-1", {"title": "Renamed"}, expected_last_edited_time="2026-09-30T10:00:00.000Z")
    assert out["updated"] and out["interpreted"]["title"] == "Renamed"
    with pytest.raises(NotionError):
        mapping.archive_event("page-1", confirm=False)
    assert mapping.archive_event("page-1", confirm=True)["archived"] is True


def test_items_between_uses_mapped_date(fake):
    connect_ok(fake); mapping.save_mapping(GOOD_MAPPING)
    items = mapping.items_between("2026-10-01", "2026-10-31")
    last_query = [b for m, p, b in fake.calls if p.endswith("/query")][-1]
    assert last_query["filter"]["and"][0]["property"] == "Due Date / Event Date" and len(items) == 3


# ----------------------------------------------------------------------------- HTTP layer
def test_router_errors_are_structured_and_tokenless(client, fake):
    fake.install_workspace(); fake.token_valid = False
    r = client.post("/notion/token", json={"token": "ntn_bad_token_value"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized" or r.json()["detail"]["kind"] == "invalid_token"
    assert "ntn_bad" not in r.text
    fake.token_valid = True
    r = client.post("/notion/token", json={"token": "ntn_good_token_value_123"})
    assert r.status_code == 200 and r.json()["workspace_name"] == "Example user HQ" and "ntn_good" not in r.text
    r = client.post("/notion/parse-url", json={"value": f"https://notion.so/ws/{DB.replace('-', '')}?v={VIEW.replace('-', '')}"})
    assert {c["kind"] for c in r.json()["candidates"]} == {"path_id", "view_id"}
    r = client.post("/notion/diagnose", json={"value": DB})
    assert r.status_code == 200 and any(d["code"] == "AMBIGUOUS_404" for d in r.json()["diagnosis"])
    r = client.post("/notion/connect", json={"data_source_id": DS})
    assert r.status_code == 200 and r.json()["connected"]
    assert client.get("/capabilities").json()["notion"]["connected"] is True
    r = client.post("/notion/events/commit", json={"proposal_id": "x", "confirm": False})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
    r = client.post("/notion/disconnect")
    assert r.json()["status"] == "DISCONNECTED" and client.get("/capabilities").json()["notion"]["connected"] is False
