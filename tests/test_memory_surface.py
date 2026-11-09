"""Surface / in-depth tiers: assignment, distillation, unified-context scoring, tiered recall, dive, dissolve protection."""
import os
import uuid

import pytest

os.environ.setdefault("PODA_BACKGROUND_SURFACES", "0")

from poda_app.runtime import ollama  # noqa: E402


def _ollama_ok():
    s = ollama.status()
    return s.get("reachable") and any(m.startswith("nomic-embed-text") for m in s.get("models", []))


needs_embeddings = pytest.mark.skipif(not _ollama_ok(), reason="Ollama with nomic-embed-text is not reachable")


def _pair(text_q, text_a, sid=None):
    from poda_app.memory import store
    sid = sid or f"surf-{uuid.uuid4()}"
    um = store.save_message("user", text_q, session_id=sid)
    store.save_message("assistant", text_a, session_id=sid)
    return um


def _raw_node(um):
    from poda_app.runtime.db import connect
    conn = connect()
    try:
        return dict(conn.execute("SELECT * FROM memory_nodes WHERE source_message_id=?", (um,)).fetchone())
    finally:
        conn.close()


def _node_for(um):
    """Fetch the pair node; if a transient embedding failure left it unassigned, run the incremental rebuild once
    (exactly what the background job does in production) so the test measures assignment logic, not Ollama hiccups."""
    from poda_app.runtime.db import connect
    from poda_app.memory import surface
    conn = connect()
    try:
        row = dict(conn.execute("SELECT * FROM memory_nodes WHERE source_message_id=?", (um,)).fetchone())
        if not row.get("surface_id"):
            surface.rebuild(conn, "incremental", distill_limit=0)
            conn.commit()
            row = dict(conn.execute("SELECT * FROM memory_nodes WHERE source_message_id=?", (um,)).fetchone())
        return row
    finally:
        conn.close()


@needs_embeddings
def test_similar_exchanges_share_a_surface_and_dissimilar_do_not(client):
    a = _node_for(_pair("What's the best warm-up before a cycling game on turf?", "Dynamic stretching, short sprints and 10 minutes of pedaling drills before any cycling game on turf."))
    b = _node_for(_pair("Any other cycling warm-up tips for game day on turf?", "Add lateral shuffles and a few bike-handling drills; keep the cycling warm-up under 20 minutes."))
    c = _node_for(_pair("How do I compute NPV for the finance homework problem set?", "Discount each cash flow by (1+r)^t and sum them; subtract the initial investment."))
    assert a["surface_id"] and b["surface_id"] and c["surface_id"]
    assert a["surface_id"] == b["surface_id"], "two cycling warm-up exchanges should join one surface"
    assert c["surface_id"] != a["surface_id"], "finance homework must not join the cycling surface"
    surf = client.get(f"/memory/surfaces/{a['surface_id']}").json()
    # other cycling exchanges created by earlier tests may legitimately share this surface
    assert surf["member_count"] >= 2 and {a["id"], b["id"]} <= {m["id"] for m in surf["members"]}
    assert surf["tier"] == "surface" and surf["radius"] >= 8


@needs_embeddings
def test_distill_updates_surface_and_respects_user_edit(client, monkeypatch):
    from poda_app.memory import surface
    from poda_app.runtime.db import connect
    a = _node_for(_pair("Which plugins do I use for vocal mixing in Pro Tools?", "You mentioned FabFilter Pro-Q and Waves CLA for vocals in Pro Tools."))
    _pair("Remind me of my vocal chain in Pro Tools again?", "Pro-Q for cleanup, CLA for compression, then a plate reverb for vocals in Pro Tools.")
    sid = a["surface_id"]
    # Deterministic model stub so the test does not depend on model output quality.
    monkeypatch.setattr(surface.ollama, "chat", lambda *args, **kw: {"message": {"content": '{"title": "Vocal mixing chain in Pro Tools", "core": "Example user mixes vocals in Pro Tools with Pro-Q, CLA compression and plate reverb.", "triggers": "vocal chain, pro tools, mixing"}'}})
    r = client.post(f"/memory/surfaces/{sid}/distill").json()
    assert r["title"] == "Vocal mixing chain in Pro Tools" and "Pro-Q" in r["summary"] and r["stale"] is False
    assert r["distill_method"].startswith("model:") or r["member_count"] < 2
    # user edit via commit → protected from re-distillation
    draft = {"id": sid, "parent_id": r["parent_id"], "title": "My vocal chain (edited)", "node_type": "surface", "summary": r["summary"], "content": r["content"],
             "reference_triggers": r["reference_triggers"], "tags": ["surface"], "x": r["x"] or 0.0, "y": r["y"] or 0.0, "z": r["z"] or 0.0, "pinned": True, "importance": 0.55}
    assert client.post("/memory/transaction/commit", json={"nodes": [draft], "deleted_ids": []}).status_code == 200
    conn = connect()
    try:
        conn.execute("UPDATE memory_nodes SET stale=1 WHERE id=?", (sid,)); conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(surface.ollama, "chat", lambda *args, **kw: {"message": {"content": '{"title": "SHOULD NOT APPLY", "core": "x", "triggers": ""}'}})
    r2 = client.post(f"/memory/surfaces/{sid}/distill").json()
    assert r2["title"] == "My vocal chain (edited)" and r2["user_edited"] is True


@needs_embeddings
def test_tiers_surface_auto_deep_and_unified_context(client):
    _pair("When is the Cymatics sample pack sale ending?", "The Cymatics sale you mentioned ends Sunday night.")
    _pair("Should I grab the Cymatics drum kit or the vocal pack?", "You leaned toward the Cymatics drum kit for the trap project.")
    q = {"query": "what did we decide about Cymatics sample packs?"}
    surf = client.post("/memory/recall", json={**q, "depth": "surface"}).json()
    assert surf["depth_used"] == "surface" and surf["results"] and all(r["tier"] in {"surface", "durable"} for r in surf["results"])
    assert surf["surfaces"] and all("content" not in r for r in surf["results"] if r["tier"] == "surface")
    auto = client.post("/memory/recall", json={**q, "depth": "auto"}).json()
    assert auto["depth_used"] == "auto" and auto["in_depth_loaded"] is False
    top = next(r for r in auto["results"] if r["tier"] == "surface")
    assert "members" in top and all(set(m) == {"id", "title"} for m in top["members"])  # titles only, no content
    assert not any(r["tier"] == "in_depth" and r.get("surface_id") for r in auto["results"])
    deep = client.post("/memory/recall", json={**q, "depth": "deep"}).json()
    assert deep["in_depth_loaded"] is True
    members = [r for r in deep["results"] if r["tier"] == "in_depth"]
    assert members and any("Cymatics" in (r.get("content") or "") for r in members)
    f = members[0]["factors"]
    assert {"unified_context", "surface_score", "surface_id", "self_cosine"} <= set(f)
    assert abs(f["unified_context"] - (0.6 * f["self_cosine"] + 0.4 * f["surface_score"])) < 0.02
    # thinking_level deep in chat semantics: auto + deep thinking = deep
    from poda_app.memory import retrieval
    assert retrieval.recall(q["query"], limit=5, depth="auto", thinking_level="deep")["depth_used"] == "deep"


@needs_embeddings
def test_unified_context_lifts_weak_member_of_top_surface(client):
    """A loosely matching exchange inside the best surface outranks an unrelated exchange with similar self cosine."""
    from poda_app.memory import surface
    from poda_app.runtime.db import connect
    a = _node_for(_pair("Plan my Guitar Center trip for the new audio interface.", "Go Saturday morning; the Guitar Center near campus has the Scarlett in stock."))
    weak = _node_for(_pair("Also check their return policy while I'm there.", "Guitar Center allows returns within 45 days with a receipt."))
    other = _node_for(_pair("Is the eBay listing for the used lens still active?", "The eBay lens listing was still active this morning."))
    conn = connect()
    try:
        surface.set_membership(conn, weak["id"], a["surface_id"])  # force membership regardless of threshold
        conn.commit()
    finally:
        conn.close()
    deep = client.post("/memory/recall", json={"query": "audio interface shopping at Guitar Center", "depth": "deep", "limit": 40}).json()
    ranks = {r["id"]: i for i, r in enumerate(deep["results"])}
    assert weak["id"] in ranks and ranks[weak["id"]] < ranks.get(other["id"], 10**6)
    w = next(r for r in deep["results"] if r["id"] == weak["id"])["factors"]
    assert w["surface_score"] > w["self_cosine"] - 0.2 and w["unified_context"] >= w["self_cosine"] - 0.05


@needs_embeddings
def test_memory_dive_tool_returns_pairs(client):
    from poda_app.agent import tools
    a = _node_for(_pair("What was the AlphaTheta controller model I liked?", "You liked the AlphaTheta DDJ-FLX10."))
    out = tools.execute("memory_dive", {"surface_id": a["surface_id"], "limit": 4})
    assert out["receipt"]["outcome"] == "succeeded"
    members = out["result"]["result"]["members"]
    assert any(m["id"] == a["id"] and "DDJ-FLX10" in (m["assistant_text"] or "") for m in members)
    by_query = tools.execute("memory_dive", {"query": "AlphaTheta controller"})
    assert by_query["result"]["result"]["surface"] is not None
    reg = client.get("/agent/tools").json()["tools"]
    assert any(t["name"] == "memory_dive" and t["available"] for t in reg)


@needs_embeddings
def test_surface_delete_requires_dissolve(client):
    a = _node_for(_pair("Which DAW did I say I'm switching to?", "You said you're moving from Pro Tools to Logic for composing."))
    sid = a["surface_id"]
    r = client.post("/memory/transaction/commit", json={"nodes": [], "deleted_ids": [sid]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "surface_has_members"
    r2 = client.post("/memory/transaction/commit", json={"nodes": [], "deleted_ids": [sid], "dissolve": True})
    assert r2.status_code == 200
    node = _raw_node(a["source_message_id"])
    assert node["surface_id"] is None
    status = client.get("/memory/surfaces/status").json()
    assert status["unassigned"] >= 1
    rebuilt = client.post("/memory/surfaces/rebuild", json={"mode": "incremental"}).json()
    assert rebuilt["unassigned"] == 0 and _node_for(a["source_message_id"])["surface_id"]


@needs_embeddings
def test_scene_exposes_surfaces_and_members(client):
    scene = client.get("/memory/open3d/scene").json()
    surfaces = [n for n in scene["nodes"] if n["node_type"] == "surface"]
    members = [n for n in scene["nodes"] if n["node_type"] == "memory" and n.get("surface_id")]
    assert surfaces and members and all("radius" in s for s in surfaces)
    assert scene["tiers"]["surfaces"] == len(surfaces) and "surface_show_default" in scene["settings"]
    links = scene["semantic_links"]
    assert any(l["relation"] == "distills" for l in links)
    checked = 0
    for s0 in surfaces:
        # members whose position the user committed keep that position by design; semantic ones orbit their surface
        for m in [m for m in members if m["surface_id"] == s0["id"] and m.get("position_source") not in {"committed", "manual"}]:
            d = sum((float(m[k] or 0) - float(s0[k] or 0)) ** 2 for k in "xyz") ** 0.5
            assert d <= s0["radius"] + 8, "members lay out around their surface"
            checked += 1
    assert checked >= 1


def test_full_rebuild_requires_confirm(client):
    r = client.post("/memory/surfaces/rebuild", json={"mode": "full"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirm_required"
