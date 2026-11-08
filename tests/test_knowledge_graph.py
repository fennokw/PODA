"""Provenance, alias lookup, and lifecycle for the typed knowledge graph."""
import json
import uuid

from poda_app.memory import knowledge, retrieval
from poda_app.runtime.db import connect, now_iso


def add_source(text):
    node_id = str(uuid.uuid4())
    with connect() as db:
        db.execute("""INSERT INTO memory_nodes(id,title,node_type,content,created_at,updated_at)
                      VALUES (?,?,'memory',?,?,?)""", (node_id, "Graph evidence", text, now_iso(), now_iso()))
    return node_id


def fact(subject, subject_kind, predicate, obj, object_kind, evidence, **kwargs):
    return {"subject": {"name": subject, "kind": subject_kind, "aliases": kwargs.get("aliases", [])},
            "predicate": predicate, "object": {"name": obj, "kind": object_kind, "aliases": []},
            "evidence": evidence, "qualifier": kwargs.get("qualifier", "")}


def save(client, node_id, facts):
    path = "/memory/knowledge/source/" + node_id
    revision = client.get(path).json()["revision"]
    return client.put(path, json={"revision": revision, "facts": facts})


def test_alias_traversal_and_feedback(client, monkeypatch):
    examples = [
        fact("Refund Rules", "policy", "approved_by", "Finance Lead", "role",
             "Refund Rules are approved by the Finance Lead.", aliases=["Refund Policy"]),
        fact("Finance Lead", "role", "held_by", "Avery North", "person",
             "Avery North holds the Finance Lead role."),
        fact("Avery North", "person", "delegates_to", "Morgan West", "person",
             "In March Avery North delegates to Morgan West.", qualifier="In March"),
    ]
    ids = [add_source(item["evidence"]) for item in examples]
    decoy = fact("Supplier Rules", "policy", "approved_by", "Founder", "role",
                 "Supplier Rules are approved by the Founder.")
    decoy_id = add_source(decoy["evidence"])
    try:
        queued = client.get("/memory/knowledge/review-queue").json()["sources"]
        assert set(ids) <= {item["id"] for item in queued}
        for node_id, item in zip(ids, examples):
            assert save(client, node_id, [item]).status_code == 200
        assert save(client, decoy_id, [decoy]).status_code == 200
        assert not set(ids) & {item["id"] for item in client.get("/memory/knowledge/review-queue").json()["sources"]}
        assert client.get("/memory/knowledge", params={"q": "Refund Policyholder"}).json()["facts"] == []
        result = client.get("/memory/knowledge", params={"q": "Who approves under the refund policy in March?"})
        facts = result.json()["facts"]
        assert result.json()["graph_ms"] >= 0
        assert [f["memory_id"] for f in facts] == ids
        assert [f["hop"] for f in facts] == [1, 2, 3]
        assert facts[-1]["qualifier"] == "In March"
        monkeypatch.setattr(retrieval.ollama, "embed", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
        db = connect()
        try:
            recalled = retrieval.recall("Refund Policy", conn=db)
            assert "Morgan West" in retrieval.context_block(recalled)
        finally:
            db.close()

        assert client.post("/memory/hindsight/feedback", json={"node_id": ids[1], "assessment": "incorrect"}).status_code == 200
        assert [f["memory_id"] for f in client.get("/memory/knowledge", params={"q": "Refund Policy"}).json()["facts"]] == ids[:1]
        client.post("/memory/hindsight/feedback", json={"node_id": ids[1], "assessment": "restore"})
        assert len(client.get("/memory/knowledge", params={"q": "Refund Policy"}).json()["facts"]) == 3
        with connect() as db:
            db.execute("UPDATE memory_nodes SET x=12 WHERE id=?", (ids[1],))
        assert len(client.get("/memory/knowledge", params={"q": "Refund Policy"}).json()["facts"]) == 3
        with connect() as db:
            db.execute("UPDATE memory_nodes SET content='A corrected source' WHERE id=?", (ids[1],))
        assert len(client.get("/memory/knowledge", params={"q": "Refund Policy"}).json()["facts"]) == 1
        assert client.delete("/memory/nodes/" + ids[0]).status_code == 200
        assert client.get("/memory/knowledge", params={"q": "Refund Policy"}).json()["facts"] == []
    finally:
        with connect() as db:
            for node_id in [*ids, decoy_id]:
                db.execute("DELETE FROM memory_nodes WHERE id=?", (node_id,))
                db.execute("DELETE FROM poda_memory_feedback WHERE poda_node_id=?", (node_id,))


def test_draft_is_review_only_and_source_revision_is_checked(client, monkeypatch):
    from poda_app.memory import knowledge_router

    text = "Riley owns the Rover project."
    node_id = add_source(text)
    supported = fact("Riley", "person", "owns", "Rover", "project", text)
    invented = {**supported, "evidence": "Riley owns the Moon."}
    path = "/memory/knowledge/source/" + node_id
    try:
        monkeypatch.setattr(knowledge_router.models, "resolve", lambda *args: {"model": "local-test"})
        monkeypatch.setattr(knowledge_router.ollama, "chat", lambda *args, **kwargs: {
            "message": {"content": json.dumps({"facts": [supported, invented]})}})
        draft = client.post(path + "/draft").json()
        assert draft["facts"] == [supported]
        assert client.get(path).json()["facts"] == []
        assert client.put(path, json={**draft, "facts": [invented]}).status_code == 422
        assert client.put(path, json=draft).status_code == 200
        assert client.put(path, json={**draft, "facts": []}).status_code == 409
        with connect() as db:
            db.execute("UPDATE memory_nodes SET speaker='assistant' WHERE id=?", (node_id,))
        assert client.get(path).json()["source"] == ""
        assert client.get(path).json()["facts"] == []
        assert knowledge.name_key("  RÍLEY—North  ") == "ríley north"
    finally:
        with connect() as db:
            db.execute("DELETE FROM memory_nodes WHERE id=?", (node_id,))
