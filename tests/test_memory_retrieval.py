"""Memory acceptance tests that need the local embedding model. Skipped truthfully if Ollama is unreachable."""
import json
import uuid

import pytest

from poda_app.runtime import ollama


def _ollama_ok():
    s = ollama.status()
    return s.get("reachable") and any(m.startswith("nomic-embed-text") for m in s.get("models", []))


pytestmark = pytest.mark.skipif(not _ollama_ok(), reason="Ollama with nomic-embed-text is not reachable")


def _draft(title, content, triggers="", parent="seed-personal-memory", **kw):
    d = {"id": f"draft-{uuid.uuid4()}", "parent_id": parent, "title": title, "node_type": "memory", "summary": content[:400], "content": content,
         "reference_triggers": triggers, "tags": ["test"], "x": 0.0, "y": 0.0, "z": 0.0, "pinned": True, "importance": 0.6}
    d.update(kw)
    return d


def test_text_edit_updates_embedding_hash(client):
    from poda_app.runtime.db import connect
    node = _draft("Cycling practice schedule", "Cycling practice is Tuesdays and Thursdays at 6pm at the north field.", "cycling, practice")
    assert client.post("/memory/transaction/commit", json={"nodes": [node], "deleted_ids": []}).status_code == 200
    conn = connect()
    h1 = conn.execute("SELECT text_hash FROM memory_embeddings WHERE node_id=?", (node["id"],)).fetchone()[0]
    conn.close()
    node["content"] = "Cycling practice moved to Mondays and Wednesdays at 7pm."
    node["summary"] = node["content"]
    assert client.post("/memory/transaction/commit", json={"nodes": [node], "deleted_ids": []}).status_code == 200
    conn = connect()
    h2 = conn.execute("SELECT text_hash FROM memory_embeddings WHERE node_id=?", (node["id"],)).fetchone()[0]
    conn.close()
    assert h1 != h2


def test_recall_finds_fact_and_explains(client):
    node = _draft("Dentist appointment", "Example user has a dentist appointment on October 9th at 2pm with Dr. Alvarez.", "dentist, teeth, appointment")
    client.post("/memory/transaction/commit", json={"nodes": [node], "deleted_ids": []})
    rec = client.post("/memory/recall", json={"query": "when is my dentist appointment?"}).json()
    assert rec["mode"] == "hybrid"
    top_ids = [r["id"] for r in rec["results"][:3]]
    assert node["id"] in top_ids
    top = next(r for r in rec["results"] if r["id"] == node["id"])
    assert set(top["factors"]) >= {"text_cosine", "lexical_bm25", "geometry_cosine", "trigger_boost", "provenance_weight"}
    assert top["factors"]["trigger_boost"] > 0


def test_committed_movement_changes_geometry_signal_but_not_text_cosine(client):
    from poda_app.runtime.db import connect
    a = _draft("Startup pitch deck", "Finish the startup pitch deck slides for the investor meeting.", "pitch deck", x=300.0, y=300.0, z=300.0)
    b = _draft("Finance homework", "Complete the finance homework problem set on NPV.", "finance homework", x=-300.0, y=-300.0, z=-300.0)
    client.post("/memory/transaction/commit", json={"nodes": [a, b], "deleted_ids": []})
    rec1 = client.post("/memory/recall", json={"query": "investor pitch deck slides", "depth": "deep", "limit": 60}).json()
    fa1 = next(r for r in rec1["results"] if r["id"] == a["id"])["factors"]
    fb1 = next(r for r in rec1["results"] if r["id"] == b["id"])["factors"]
    # Move b right next to a and commit → geometry signal for b rises, text cosine unchanged.
    b["x"], b["y"], b["z"] = a["x"] + 0.5, a["y"] + 0.5, a["z"] + 0.5
    client.post("/memory/transaction/commit", json={"nodes": [a, b], "deleted_ids": []})
    rec2 = client.post("/memory/recall", json={"query": "investor pitch deck slides", "depth": "deep", "limit": 60}).json()
    fb2 = next(r for r in rec2["results"] if r["id"] == b["id"])["factors"]
    assert abs(fb2["text_cosine"] - fb1["text_cosine"]) < 0.02
    assert fb2["geometry_cosine"] >= fb1["geometry_cosine"]
    conn = connect()
    geo_links = conn.execute("SELECT COUNT(*) FROM memory_links WHERE relation='geometry_cosine' AND source_id=? AND target_id=?", (b["id"], a["id"])).fetchone()[0]
    conn.close()
    assert geo_links == 1


def test_superseded_memory_is_annotated_not_asserted(client):
    from poda_app.runtime.db import connect, now_iso
    old = _draft("Old apartment", "Example user lives at 12 Elm Street.", "address, where I live")
    new = _draft("New apartment", "Example user moved to 88 Harbor Way in September 2026.", "address, where I live")
    client.post("/memory/transaction/commit", json={"nodes": [old, new], "deleted_ids": []})
    conn = connect()
    conn.execute("UPDATE memory_nodes SET superseded_by=?, valid_until=? WHERE id=?", (new["id"], now_iso(), old["id"]))
    conn.commit(); conn.close()
    rec = client.post("/memory/recall", json={"query": "where does Example user live now?"}).json()
    ranks = {r["id"]: i for i, r in enumerate(rec["results"])}
    assert ranks[new["id"]] < ranks[old["id"]]
    from poda_app.memory.retrieval import context_block
    block = context_block(rec)
    assert "SUPERSEDED" in block


def test_prior_assistant_claims_are_contextual_not_authoritative(client):
    """A PODA reply (standalone or folded into a question→response pair) is never ranked as an authoritative user fact."""
    from poda_app.memory import store
    store.save_message("assistant", "Your flight to Denver departs at 6:45am on October 12.", session_id="flight-test")
    rec = client.post("/memory/recall", json={"query": "when is my flight to Denver?", "depth": "deep"}).json()
    assert rec["depth_used"] == "deep"
    in_depth = [r for r in rec["results"] if r.get("tier") == "in_depth"]
    assert in_depth, rec["results"][:3]
    hit = next(r for r in in_depth[:3] if "Denver" in ((r.get("content") or "") + (r.get("summary") or "")))
    assert hit["speaker"] in {"assistant", "exchange"} and "not authoritative" in hit["provenance"]
    assert hit["factors"]["provenance_weight"] < 1.0
    # surface tier never exposes the raw reply text as a fact; it is a distilled core with the surface provenance
    surf = client.post("/memory/recall", json={"query": "when is my flight to Denver?", "depth": "surface"}).json()
    assert all(r["tier"] in {"surface", "durable"} for r in surf["results"])
