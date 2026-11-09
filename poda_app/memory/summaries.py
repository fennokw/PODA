"""Progressive session compaction: old turns become source-linked summary nodes (visible, searchable)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..runtime import config, ollama, models
from ..runtime.db import connect, now_iso, rows, one
from . import store

COMPACT_AFTER = 40     # messages without a summary before compaction kicks in
CHUNK = 20             # messages summarized per pass


def maybe_compact(session_id: str | None = None) -> dict[str, Any] | None:
    conn = connect()
    try:
        summarized: set[str] = set()
        for r in rows(conn, "SELECT source_message_ids_json FROM session_summaries"):
            try:
                summarized.update(json.loads(r["source_message_ids_json"]))
            except Exception:
                pass
        candidates = [m for m in rows(conn, "SELECT id, role, content, created_at FROM messages ORDER BY created_at ASC") if m["id"] not in summarized]
        if len(candidates) < COMPACT_AFTER:
            return None
        batch = candidates[:CHUNK]
    finally:
        conn.close()
    reg = models.registry()
    model = reg["profiles"]["fast"]["model"] or reg["profiles"]["balanced"]["model"]
    if not model:
        return None
    transcript = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in batch)
    prompt = ("Summarize the following PODA conversation excerpt into dated, factual bullet points. Mark user-stated facts as FACT, "
              "assistant claims as ASSISTANT-CLAIM, and anything uncertain as UNCERTAIN. Do not invent. Keep under 180 words.\n\n" + transcript)
    try:
        result = ollama.chat([{"role": "user", "content": prompt}], model, options={"temperature": 0.05, "num_ctx": 8192, "num_predict": 400}, think=False if model.startswith("qwen3") else None)
        text = (result.get("message", {}).get("content") or "").strip()
    except Exception:
        return None
    if not text:
        return None
    conn = connect()
    try:
        sid = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        span = f"{batch[0]['created_at'][:10]} → {batch[-1]['created_at'][:10]}"
        conn.execute("""INSERT INTO memory_nodes (id, parent_id, title, node_type, summary, content, reference_triggers, tags_json, pinned, importance, memory_kind, speaker, confidence, source_ref, created_at, updated_at)
                        VALUES (?, ?, ?, 'memory', ?, ?, ?, ?, 0, 0.45, 'summary', 'poda-summary', 0.6, ?, ?, ?)""",
                     (node_id, store.CONVERSATION_CLOUD, f"Summary: {span}", text[:420], text[:12000], "earlier conversation, what did we discuss, recap", json.dumps(["summary", "conversation", "compaction"]),
                      json.dumps([m["id"] for m in batch]), now_iso(), now_iso()))
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.5, ?, ?)",
                     (str(uuid.uuid4()), store.CONVERSATION_CLOUD, node_id, now_iso(), now_iso()))
        conn.execute("INSERT INTO session_summaries (id, session_id, summary, source_message_ids_json, confidence, model, node_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (sid, session_id, text, json.dumps([m["id"] for m in batch]), 0.6, model, node_id, now_iso()))
        conn.commit()
        return {"summary_id": sid, "node_id": node_id, "messages": len(batch)}
    finally:
        conn.close()
