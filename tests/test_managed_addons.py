"""Verify that local users can set up optional tools without environment flags."""
import json
from types import SimpleNamespace

from poda_app.agent import software
from poda_app.memory import hindsight_bridge, hindsight_runtime
from poda_app.runtime import skill_library
from poda_app.runtime.db import get_setting, set_setting


def test_download_catalog_skill_and_enable_programs(client, tmp_path, monkeypatch):
    monkeypatch.delenv("PODA_ALLOW_EXTERNAL_SOFTWARE", raising=False)
    monkeypatch.setattr(software, "OFFICIAL_CATALOG", tmp_path)
    original = get_setting("software_external_execution", "")
    skill_id = None

    def response(service, method, url, **kwargs):
        assert service == "addon_catalog" and method == "GET"
        assert url.startswith(software._CATALOG_RAW)
        if url.endswith("registry.json"):
            data = {"clis": [{"name": "sample-audio", "display_name": "Sample Audio",
                     "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=sample/agent-harness",
                     "skill_md": "skills/cli-anything-sample-audio/SKILL.md"},
                    {"name": "unknown", "install_cmd": "pip install https://example.invalid/unknown"}]}
            return SimpleNamespace(content=json.dumps(data).encode(), json=lambda: data, raise_for_status=lambda: None)
        return SimpleNamespace(content=b"# Sample Audio\nUse the track list before editing.", raise_for_status=lambda: None)

    monkeypatch.setattr(software.egress, "guarded_request", response)
    try:
        assert client.put("/software/execution", json={"enabled": True}).status_code == 400
        assert client.post("/software/catalog/download", json={"confirm": True}).status_code == 200
        entry = client.get("/software/sample-audio").json()
        assert software._CATALOG_REVISION in entry["install_cmd"]
        assert client.get("/software/unknown").status_code == 404
        assert client.post("/software/sample-audio/install", json={"confirm": True, "allow_network": True}).status_code == 400
        saved = client.post("/software/sample-audio/save-skill", json={"confirm": True}).json()
        skill_id = saved["id"]
        assert not saved["enabled"]
        assert client.put(f"/personalize/skills/{skill_id}", json={"enabled": True}).status_code == 200
        assert "Use the track list" in skill_library.hint_for_task("Use Sample Audio to inspect tracks")
        assert client.put("/software/execution", json={"enabled": True, "confirm": True}).json()["execution_allowed"]
        assert client.get("/software").json()["execution_allowed"]
        assert not client.put("/software/execution", json={"enabled": False}).json()["execution_allowed"]
    finally:
        if skill_id:
            client.delete(f"/personalize/skills/{skill_id}")
        set_setting("software_external_execution", original)
        software._CACHE["catalog"] = None
        software.refresh_tools()


def test_hindsight_setup_and_document_id_recall(client, monkeypatch):
    previous = get_setting("hindsight_local_enabled", "false")
    monkeypatch.setattr(hindsight_bridge, "_background_reconcile", lambda: None)
    calls = []
    monkeypatch.setattr(hindsight_runtime, "install_and_start", lambda: calls.append("install"))
    monkeypatch.setattr(hindsight_runtime, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(hindsight_runtime, "status", lambda: {
        "installed": "install" in calls, "local_server_reachable": calls == ["install"]})
    monkeypatch.setattr(hindsight_bridge, "_request", lambda *args: {
        "results": [{"document_id": "poda-node-abc"}, {"document_id": "other"},
                    {"document_id": "poda-node-abc"}]})
    try:
        assert client.post("/memory/hindsight/setup", json={}).status_code == 400
        assert not calls
        status = client.post("/memory/hindsight/setup", json={"confirm_download": True}).json()
        assert status["enabled"] and status["local_server_reachable"]
        assert hindsight_bridge.recall_node_ids("sample") == ["abc"]
        assert not client.put("/memory/hindsight/config", json={"enabled": False}).json()["enabled"]
        assert calls == ["install", "stop"]
    finally:
        set_setting("hindsight_local_enabled", previous)


def test_developer_mode_preference_round_trip(client):
    try:
        assert not client.get("/system/view-preferences").json()["developer_mode"]
        assert client.put("/system/view-preferences", json={"developer_mode": True}).status_code == 200
        assert client.get("/system/view-preferences").json()["developer_mode"]
    finally:
        client.put("/system/view-preferences", json={"developer_mode": False})


def test_native_hindsight_sidecar(tmp_path, monkeypatch):
    """Opt-in smoke for the pinned Hindsight package on hosted desktop runners."""
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import pytest

    if os.environ.get("PODA_TEST_NATIVE_HINDSIGHT") != "1":
        pytest.skip("Set PODA_TEST_NATIVE_HINDSIGHT=1 for the isolated native sidecar smoke.")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("Embedded PostgreSQL requires an unprivileged user.")

    class FakeInference(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/embeddings"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            values = body["input"] if isinstance(body["input"], list) else [body["input"]]
            output = json.dumps({"object": "list", "model": body["model"], "data": [
                {"object": "embedding", "index": i, "embedding": [0.05] * 768} for i in range(len(values))
            ], "usage": {"prompt_tokens": 1, "total_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

    with ThreadingHTTPServer(("127.0.0.1", 0), FakeInference) as inference:
        worker = Thread(target=inference.serve_forever, daemon=True)
        worker.start()
        local = f"http://127.0.0.1:{inference.server_port}"
        monkeypatch.setattr(hindsight_runtime, "_ROOT", tmp_path / "hindsight")
        monkeypatch.setattr(hindsight_runtime, "_RUNTIME", tmp_path / "hindsight" / "runtime")
        monkeypatch.setattr(hindsight_runtime, "_LOG", tmp_path / "hindsight.log")
        monkeypatch.setattr(hindsight_runtime, "_models", lambda: (local, "sample-chat", "sample-embed"))
        monkeypatch.setattr(hindsight_runtime.egress, "offline_mode", lambda: False)
        monkeypatch.setattr(hindsight_runtime.egress, "_log", lambda *args: None)
        settings = {}
        monkeypatch.setattr(hindsight_runtime, "get_setting", settings.get)
        monkeypatch.setattr(hindsight_runtime, "set_setting", settings.__setitem__)
        environment = hindsight_runtime._environment
        monkeypatch.setattr(hindsight_runtime, "_environment",
                            lambda: {**environment(), "HINDSIGHT_API_RETAIN_EXTRACTION_MODE": "chunks"})
        try:
            hindsight_runtime.install_and_start()
            assert hindsight_runtime.status()["local_server_reachable"]
            path = hindsight_bridge._path("/memories")
            retained = hindsight_bridge._request("POST", path, {"async": False, "items": [
                {"content": "The workshop prototype uses a ceramic rotor.", "document_id": "poda-node-demo"}
            ]})
            assert retained["success"]
            result = hindsight_bridge._request("POST", path + "/recall", {"query": "ceramic rotor", "budget": "low"})
            assert "poda-node-demo" in {item.get("document_id") for item in result["results"]}
        except Exception as exc:
            pytest.fail(f"{exc}\n{hindsight_runtime.diagnostics()}")
        finally:
            hindsight_runtime.stop()
            inference.shutdown()
            worker.join(timeout=5)
