"""Sequential question→response pair nodes: one visible node per exchange, both message ids covered."""
import os
import uuid

os.environ.setdefault("PODA_BACKGROUND_SURFACES", "0")


def _counts():
    from poda_app.runtime.db import connect
    conn = connect()
    try:
        orphans = conn.execute("""SELECT COUNT(*) FROM messages m WHERE NOT EXISTS (
            SELECT 1 FROM memory_nodes n WHERE n.source_message_id = m.id OR n.response_message_id = m.id)""").fetchone()[0]
        return orphans
    finally:
        conn.close()


def test_pair_creates_one_node_covering_both_messages(client):
    from poda_app.memory import store
    from poda_app.runtime.db import connect
    sid = f"pair-{uuid.uuid4()}"
    um = store.save_message("user", "How many cycling practices are there this week?", session_id=sid)
    am = store.save_message("assistant", "Two: Tuesday and Thursday at 6pm.", "llama3.2:3b", session_id=sid)
    conn = connect()
    try:
        node = conn.execute("SELECT * FROM memory_nodes WHERE source_message_id=?", (um,)).fetchone()
        assert node is not None and node["response_message_id"] == am
        assert node["memory_kind"] == "exchange" and node["speaker"] == "exchange"
        assert node["user_text"].startswith("How many cycling") and node["assistant_text"].startswith("Two:")
        assert node["content"].startswith("You: How many cycling") and "PODA: Two:" in node["content"]
        assert node["title"].startswith("Q: ")
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE source_message_id=?", (am,)).fetchone()[0] == 0  # no standalone reply node
    finally:
        conn.close()
    assert _counts() == 0
    api_node = next(n for n in client.get("/memory").json()["nodes"] if n.get("source_message_id") == um)
    assert api_node["tier"] == "in_depth" and api_node["response_message_id"] == am and api_node["user_text"]


def test_durable_fact_keeps_kind_but_absorbs_reply(client):
    from poda_app.memory import store
    from poda_app.runtime.db import connect
    sid = f"pair-{uuid.uuid4()}"
    um = store.save_message("user", "Remember that my thesis deadline is November 14 and it matters more than anything else this term.", session_id=sid)
    store.save_message("assistant", "Noted: thesis deadline November 14, top priority.", session_id=sid)
    conn = connect()
    try:
        node = conn.execute("SELECT memory_kind, parent_id, response_message_id, title FROM memory_nodes WHERE source_message_id=?", (um,)).fetchone()
    finally:
        conn.close()
    assert node["memory_kind"] == "durable" and node["response_message_id"] and not node["title"].startswith("Q: ")
    assert node["parent_id"] != "seed-conversation-memory"


def test_reply_without_user_turn_gets_standalone_visible_node(client):
    from poda_app.memory import store
    from poda_app.runtime.db import connect
    sid = f"gate-{uuid.uuid4()}"
    # Exhaust pairing: a user turn that is already paired, then an extra assistant message in a brand-new session.
    um = store.save_message("user", "Quick one: what time is it in Tokyo?", session_id=sid)
    store.save_message("assistant", "I cannot verify live time zones here; it is UTC+9 year-round.", session_id=sid)
    conn = connect()
    try:
        conn.execute("UPDATE messages SET created_at='2000-01-01T00:00:00+00:00' WHERE session_id=?", (sid,))  # push far into the past
        conn.commit()
    finally:
        conn.close()
    am = store.save_message("assistant", "Report what you did (continuation turn).", session_id=f"other-{uuid.uuid4()}")
    conn = connect()
    try:
        node = conn.execute("SELECT speaker, memory_kind FROM memory_nodes WHERE source_message_id=?", (am,)).fetchone()
    finally:
        conn.close()
    assert node is not None and node["speaker"] == "assistant" and node["memory_kind"] == "conversation"
    assert _counts() == 0


def test_pair_history_migrates_legacy_history(client):
    """Seed an old-style history (separate user and assistant nodes) and prove the migration folds them."""
    import json
    from poda_app.memory import store
    from poda_app.runtime.db import connect, now_iso
    conn = connect()
    try:
        sid = f"legacy-{uuid.uuid4()}"
        ids = []
        for i, (role, text) in enumerate([("user", "Legacy question about the startup pitch deck?"), ("assistant", "Legacy answer: finish the deck slides by Friday."),
                                            ("user", "Legacy follow-up: and the finance homework?"), ("assistant", "Legacy answer two: NPV problem set is due Monday.")]):
            mid = str(uuid.uuid4()); ids.append(mid)
            ts = f"2026-09-20T10:0{i}:00+00:00"
            conn.execute("INSERT INTO messages (id, role, content, model, created_at, session_id) VALUES (?,?,?,?,?,?)", (mid, role, text, None, ts, sid))
            store.ingest_chat_turn(conn, role, text, source_message_id=mid, created_at=ts)  # legacy: one node per message
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE source_message_id IN (?,?,?,?)", tuple(ids)).fetchone()[0] == 4
        result = store.pair_history(conn)
        conn.commit()
        assert result["paired"] >= 2 and result["standalone_removed"] >= 2
        n1 = conn.execute("SELECT response_message_id, content FROM memory_nodes WHERE source_message_id=?", (ids[0],)).fetchone()
        assert n1["response_message_id"] == ids[1] and "PODA: Legacy answer: finish" in n1["content"]
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE source_message_id IN (?,?)", (ids[1], ids[3])).fetchone()[0] == 0
        again = store.pair_history(conn)  # idempotent
        assert again["paired"] == 0
    finally:
        conn.close()
    assert _counts() == 0


def test_deleting_pair_node_removes_both_message_rows(client):
    from poda_app.memory import store
    from poda_app.runtime.db import connect
    sid = f"del-{uuid.uuid4()}"
    um = store.save_message("user", "Please forget this: my old gym code was 4411.", session_id=sid)
    am = store.save_message("assistant", "Understood, I will not keep that.", session_id=sid)
    node = next(n for n in client.get("/memory").json()["nodes"] if n.get("source_message_id") == um)
    r = client.post("/memory/transaction/commit", json={"nodes": [], "deleted_ids": [node["id"]]})
    assert r.status_code == 200, r.text
    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE id IN (?,?)", (um, am)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE id=?", (node["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def test_recent_context_and_history_render_pairs_as_two_turns(client):
    from poda_app.memory import store
    sid = f"ctx-{uuid.uuid4()}"
    store.save_message("user", "Context check: what did I say my favorite editor was?", session_id=sid)
    store.save_message("assistant", "You have not told me your favorite editor.", session_id=sid)
    ctx = store.recent_context(session_id=sid)
    assert "user: Context check" in ctx and "assistant (prior PODA response; not authoritative): You have not told me" in ctx
    history = client.get("/chat/history", params={"limit": 6}).json()["messages"]
    speakers = [m["speaker"] for m in history[-2:]]
    assert speakers == ["user", "assistant"] and history[-1]["content"].startswith("You have not told me")


def test_every_message_has_visible_node_including_replies(client):
    assert _counts() == 0
