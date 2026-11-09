import json


def test_build_identity_public(client):
    r = client.get("/system/build")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "PODA" and len(body["build_id"]) == 12 and body["memory_viewer"] == "/memory-viewer"


def test_protected_routes_require_session_and_origin(client):
    from fastapi.testclient import TestClient
    from poda_app.main import app
    with TestClient(app, base_url="http://127.0.0.1:8799") as fresh:
        assert fresh.get("/capabilities").status_code == 401
        fresh.get("/")
        assert fresh.get("/capabilities").status_code == 200
        assert fresh.get("/capabilities", headers={"Origin": "http://evil.example"}).status_code == 403
        assert fresh.get("/capabilities", headers={"Host": "attacker.local"}).status_code == 421


def test_security_headers(client):
    r = client.get("/")
    assert "Content-Security-Policy" in r.headers and "'self'" in r.headers["Content-Security-Policy"]
    assert "http" not in r.headers["Content-Security-Policy"].replace("'self'", "")  # no remote sources
    assert r.headers["Cache-Control"].startswith("no-store")


def test_capabilities_derive_from_runtime(client):
    cap = client.get("/capabilities").json()
    assert cap["local_only"] is True and cap["cloud_models"] is False
    assert cap["filesystem"]["general_user_file_read"] is False
    assert cap["notion"]["connected"] is False and cap["notion"]["status"] == "DISCONNECTED"


def test_memory_seed_and_graph(client):
    g = client.get("/memory").json()
    ids = {n["id"] for n in g["nodes"]}
    assert "root-personal-organization" in ids and "seed-conversation-memory" in ids
    assert all(l["source_id"] in ids and l["target_id"] in ids for l in g["links"])


def test_memory_search_lexical(client):
    r = client.get("/memory/search", params={"q": "zzqx-nonexistent-term"}).json()
    assert r["count"] == 0


def test_projects_prioritize_explainable(client):
    client.post("/projects", json={"name": "Thesis draft", "importance": 9, "difficulty": 7, "hours_remaining": 12, "deadline": "2026-10-05"})
    client.post("/projects", json={"name": "Quick email", "importance": 4, "difficulty": 2, "hours_remaining": 0.5})
    ranked = client.get("/projects/prioritize").json()["ranked"]
    assert ranked and ranked[0]["rank"] == 1 and "rationale" in ranked[0] and "confidence" in ranked[0]


def test_privacy_status_shape(client):
    s = client.get("/privacy/status").json()
    assert "encryption" in s and "threat_model" in s and "egress" in s
    assert s["egress"]["allowlist"]["notion"] == ["api.notion.com"]
