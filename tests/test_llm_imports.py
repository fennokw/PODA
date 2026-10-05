from __future__ import annotations

import io
import json
import zipfile

from poda_app.runtime.db import connect, get_setting


def _chatgpt_zip() -> bytes:
    conversations = [
        {
            "id": "conv-code",
            "title": "Coding workflow",
            "create_time": 1,
            "mapping": {
                "a": {"message": {"id": "m1", "author": {"role": "user"}, "create_time": 1, "content": {"parts": ["I prefer concise patch-style code changes and I am building a local assistant."]}}},
                "b": {"message": {"id": "m2", "author": {"role": "assistant"}, "create_time": 2, "content": {"parts": ["Understood. I will provide focused patches."]}}},
            },
        },
        {
            "id": "conv-music",
            "title": "Music organization",
            "create_time": 3,
            "mapping": {
                "a": {"message": {"id": "m3", "author": {"role": "user"}, "create_time": 3, "content": {"parts": ["My music workflow uses cue points and I like organizing tracks before performance."]}}},
                "b": {"message": {"id": "m4", "author": {"role": "assistant"}, "create_time": 4, "content": {"parts": ["We can organize that workflow."]}}},
            },
        },
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations.json", json.dumps(conversations))
    return buf.getvalue()


def test_chatgpt_export_import_creates_visible_memories_and_clouds(client, monkeypatch):
    # Keep the test deterministic/offline; the importer must still succeed without Ollama.
    from poda_app.imports import service
    monkeypatch.setattr(service, "derive_profile", lambda convs: {
        "summary": "User prefers concise patches and organized workflows.",
        "communication_preferences": ["concise"], "workflow_preferences": ["organized"],
        "recurring_topics": ["coding", "music"], "response_preferences": ["patches"], "source": "test",
    })
    from poda_app.imports import archive
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 50_000_000)

    data = _chatgpt_zip()
    r = client.post("/imports/llm-export", files={"file": ("chatgpt-export.zip", data, "application/zip")}, data={"apply_profile": "true"})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["status"] == "imported"
    assert payload["provider"] == "chatgpt"
    assert payload["conversations"] == 2
    assert payload["memories"] >= 3  # two exchanges + visible profile node
    assert len(payload["clouds"]) >= 1

    conn = connect()
    try:
        imported = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE source_ref LIKE 'llm-import:%'").fetchone()[0]
        domains = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='domain' AND tags_json LIKE '%topic-cloud%'").fetchone()[0]
        exchange = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE memory_kind='exchange' AND source_ref LIKE 'llm-import:%'").fetchone()[0]
    finally:
        conn.close()
    assert imported >= 3
    assert domains >= 1
    assert exchange == 2
    assert "concise patches" in get_setting("imported_user_profile")


def test_import_is_idempotent_by_archive_fingerprint(client, monkeypatch):
    from poda_app.imports import service
    monkeypatch.setattr(service, "derive_profile", lambda convs: {"summary": "test", "source": "test"})
    data = _chatgpt_zip()
    first = client.post("/imports/llm-export", files={"file": ("same.zip", data, "application/zip")}, data={"apply_profile": "false"})
    assert first.status_code == 200
    second = client.post("/imports/llm-export", files={"file": ("same.zip", data, "application/zip")}, data={"apply_profile": "false"})
    assert second.status_code == 200
    assert second.json()["status"] == "already_imported"


def test_zip_traversal_is_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../conversations.json", "[]")
    r = client.post("/imports/llm-export", files={"file": ("bad.zip", buf.getvalue(), "application/zip")}, data={"apply_profile": "false"})
    assert r.status_code == 400
    assert "Unsafe archive path" in r.text


def test_imported_profile_is_injected_as_fallible_personalization_context(client):
    from poda_app.chat.prompt import imported_profile_block
    block = imported_profile_block()
    assert "IMPORTED PERSONALIZATION PROFILE" in block
    assert "never overrides" in block


def test_future_chat_can_match_imported_topic_cloud(client):
    from poda_app.memory import store
    conn = connect()
    try:
        cloud = conn.execute("SELECT id,title,tags_json FROM memory_nodes WHERE node_type='domain' AND tags_json LIKE '%topic-cloud%' LIMIT 1").fetchone()
        assert cloud is not None
        import json as _json
        tags = _json.loads(cloud["tags_json"] or "[]")
        cue = next((t for t in tags if t not in {"imported", "topic-cloud"}), cloud["title"])
        chosen = store.parent_for_text(f"I want to continue working on {cue} today", conn)
        assert chosen == cloud["id"] or chosen in {"seed-project-priorities", "seed-calendar-events", "seed-email-triage", "seed-personal-memory"}
    finally:
        conn.close()
