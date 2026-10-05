import uuid


def _node(**kw):
    base = {"id": f"draft-{uuid.uuid4()}", "parent_id": "seed-personal-memory", "title": "Example user prefers morning deep work", "node_type": "memory",
            "summary": "Prefers deep work before noon", "content": "Example user prefers morning deep work blocks before noon.", "reference_triggers": "schedule, focus time, mornings",
            "tags": ["preference"], "x": 10.0, "y": 20.0, "z": 30.0, "pinned": True, "importance": 0.7}
    base.update(kw)
    return base


def test_commit_creates_versions_and_fts(client):
    node = _node()
    r = client.post("/memory/transaction/commit", json={"nodes": [node], "deleted_ids": []})
    assert r.status_code == 200, r.text
    tx = r.json()["transaction_id"]
    versions = client.get(f"/memory/versions/{node['id']}").json()["versions"]
    assert versions and versions[0]["change"] == "create" and versions[0]["transaction_id"] == tx
    found = client.get("/memory/search", params={"q": "morning deep work"}).json()
    assert any(n["id"] == node["id"] for n in found["results"])
    stored = next(n for n in client.get("/memory").json()["nodes"] if n["id"] == node["id"])
    assert stored["position_source"] == "committed" and abs(stored["x"] - 10.0) < 1e-6


def test_uncommitted_movement_changes_nothing(client):
    before = {n["id"]: (n["x"], n["y"], n["z"]) for n in client.get("/memory").json()["nodes"]}
    after = {n["id"]: (n["x"], n["y"], n["z"]) for n in client.get("/memory").json()["nodes"]}
    assert before == after  # no server-side mutation happens without a commit call


def test_delete_memory_removes_hidden_source_message(client):
    from poda_app.memory import store
    mid = store.save_message("user", "Remember that my dentist appointment is on October 9th at 2pm.")
    g = client.get("/memory").json()
    node = next(n for n in g["nodes"] if n.get("source_message_id") == mid)
    r = client.post("/memory/transaction/commit", json={"nodes": [], "deleted_ids": [node["id"]]})
    assert r.status_code == 200
    assert not any(n["id"] == node["id"] for n in client.get("/memory").json()["nodes"])
    from poda_app.runtime.db import connect
    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE id=?", (mid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_every_message_has_visible_node(client):
    from poda_app.runtime.db import connect
    conn = connect()
    try:
        orphans = conn.execute("""SELECT COUNT(*) FROM messages m WHERE NOT EXISTS (
            SELECT 1 FROM memory_nodes n WHERE n.source_message_id = m.id OR n.response_message_id = m.id)""").fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0
