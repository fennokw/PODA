"""Crash-safe pending embeddings. Never hold a SQLite write lock during Ollama IO."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Any

from ..runtime import config, ollama
from ..runtime.db import connect, now_iso, rows
from . import store, layout


def enqueue(conn, node_ids: Iterable[str]) -> None:
    for node_id in set(node_ids):
        row = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (node_id,)).fetchone()
        if row is None or row["node_type"] not in ("memory", "surface"):
            conn.execute("DELETE FROM memory_embedding_jobs WHERE node_id=?", (node_id,))
            continue
        digest = hashlib.sha256(store.embedding_text(dict(row)).encode("utf-8", "ignore")).hexdigest()
        conn.execute("""INSERT INTO memory_embedding_jobs(node_id, content_hash, status, updated_at)
            VALUES (?, ?, 'pending', ?) ON CONFLICT(node_id) DO UPDATE SET
            content_hash=excluded.content_hash, status='pending', attempts=0, last_error=NULL,
            updated_at=excluded.updated_at""", (node_id, digest, now_iso()))


def pending_count() -> int:
    conn = connect()
    try:
        return int(conn.execute("SELECT count(*) FROM memory_embedding_jobs").fetchone()[0])
    finally:
        conn.close()


def refresh_pending(limit: int = 24) -> dict[str, Any]:
    """Embed missing text with NO transaction open. Validate hash before applying results."""
    conn = connect()
    refreshed, error = 0, None
    try:
        jobs = rows(conn, "SELECT node_id, content_hash FROM memory_embedding_jobs ORDER BY updated_at LIMIT ?", (max(1, min(limit, 128)),))
        for job in jobs:
            row = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (job["node_id"],)).fetchone()
            if row is None or row["node_type"] not in ("memory", "surface"):
                conn.execute("DELETE FROM memory_embedding_jobs WHERE node_id=?", (job["node_id"],))
                conn.commit()
                continue
            content = store.embedding_text(dict(row))
            digest = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
            if digest != job["content_hash"]:
                enqueue(conn, [job["node_id"]]); conn.commit()
                continue
            try:
                # No open write transaction while Ollama is called.
                vectors = ollama.embed([content])
                vector = store.normalize(vectors[0])
                if not vector: raise ValueError("Empty embedding")
            except Exception as exc:
                error = type(exc).__name__
                conn.execute("""UPDATE memory_embedding_jobs SET attempts=attempts+1,
                    last_error=?, updated_at=? WHERE node_id=?""", (error, now_iso(), job["node_id"]))
                conn.commit()
                break  # Don't hammer an offline embedding service.
            # Discard stale inference if another writer changed this memory during the request.
            current = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (job["node_id"],)).fetchone()
            if current is None or hashlib.sha256(store.embedding_text(dict(current)).encode("utf-8", "ignore")).hexdigest() != digest:
                if current is not None: enqueue(conn, [job["node_id"]])
                else: conn.execute("DELETE FROM memory_embedding_jobs WHERE node_id=?", (job["node_id"],))
                conn.commit(); continue
            conn.execute("""INSERT INTO memory_embeddings(node_id,model,text_hash,vector_json,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET model=excluded.model,text_hash=excluded.text_hash,
                vector_json=excluded.vector_json,updated_at=excluded.updated_at""",
                (job["node_id"], config.EMBED_MODEL, digest, json.dumps(vector), now_iso()))
            conn.execute("DELETE FROM memory_embedding_jobs WHERE node_id=?", (job["node_id"],))
            conn.commit(); refreshed += 1
        if refreshed:
            # Cached vectors only; geometric and semantic links remain usable even when Ollama drops out.
            vectors = store.ensure_embeddings(conn, allow_network=False)
            layout.refresh_semantic_links(conn, vectors)
            conn.commit()
        count = int(conn.execute("SELECT count(*) FROM memory_embedding_jobs").fetchone()[0])
        return {"refreshed": refreshed, "pending": count, "error": error}
    finally:
        conn.close()
