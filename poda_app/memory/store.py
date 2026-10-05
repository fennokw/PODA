"""Canonical memory store: nodes, links, embeddings cache, chat ingestion, seed reconciliation.

Invariant preserved from v0.3.11: every chat turn PODA may later retrieve has a visible memory node
with `source_message_id` set, classified by `memory_kind` and `speaker`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Any, Iterable

from ..runtime import config
from ..runtime.db import connect, now_iso, rows, one
from ..runtime import ollama

ROOT_ID = "root-personal-organization"
CONVERSATION_CLOUD = "seed-conversation-memory"
PERSONAL_CLOUD = "seed-personal-memory"
SEED_DOMAINS = [
    ("Email Triage", "Inbox review, follow-ups, commitments, receipts, and scheduling signals.", ["email", "inbox"], -130, 35, 35),
    ("Calendar + Events", "Detected events, deadlines, time blocks, and schedule pressure.", ["calendar", "events"], 115, 75, -30),
    ("Project Priorities", "Project ranking by urgency, importance, difficulty, time remaining, and completion time.", ["projects", "priority"], -70, -105, -55),
    ("Personal Memory", "Stable preferences, goals, workflow patterns, and context.", ["memory", "preferences"], 115, -80, 55),
    ("Conversation Memory", "Visible episodic context mirrored from chat turns that PODA may reference later.", ["conversation", "chat", "episodic"], 10, 150, 95),
]


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def embedding_text(row: Any) -> str:
    try:
        tags = ", ".join(str(t) for t in json.loads(_get(row, "tags_json") or "[]")[:12])
    except Exception:
        tags = ""
    kind = str(_get(row, "memory_kind") or "durable")
    speaker = str(_get(row, "speaker") or "")
    paired = bool(_get(row, "response_message_id"))
    if kind == "surface":
        provenance = "Surface memory — distilled core meaning shared by several related exchanges."
    elif paired and kind != "durable":
        provenance = "User question with PODA's reply (reply is contextual, not authoritative)."
    elif kind == "conversation" and speaker == "assistant":
        provenance = "Prior PODA conversation response — contextual, not authoritative user fact."
    elif kind == "conversation":
        provenance = "User conversation context."
    elif kind == "summary":
        provenance = "Source-linked session summary."
    elif paired:
        provenance = "Durable user memory, with PODA's reply attached (reply is contextual, not authoritative)."
    else:
        provenance = "Durable user memory."
    triggers = str(_get(row, "reference_triggers") or "").strip()
    parts = [provenance, str(_get(row, "title") or ""), str(_get(row, "summary") or ""), str(_get(row, "content") or "")]
    if triggers:
        parts.append(f"Reference triggers: {triggers}")
    if tags:
        parts.append(f"Tags: {tags}")
    return "\n".join(parts).strip()[:7000]


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return vector if norm <= 1e-12 else [v / norm for v in vector]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def ensure_embeddings(conn, *, allow_network: bool = True) -> dict[str, list[float]]:
    """Return normalized embeddings for all memory nodes, caching them (versioned by model) in SQLite."""
    memory_rows = rows(conn, "SELECT * FROM memory_nodes WHERE node_type IN ('memory','surface') ORDER BY updated_at ASC")
    if not memory_rows:
        return {}
    cached = {r["node_id"]: r for r in rows(conn, "SELECT * FROM memory_embeddings WHERE model=?", (config.EMBED_MODEL,))}
    vectors: dict[str, list[float]] = {}
    missing: list[tuple[dict[str, Any], str]] = []
    for row in memory_rows:
        text = embedding_text(row)
        digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
        hit = cached.get(row["id"])
        if hit and hit["text_hash"] == digest:
            try:
                vec = normalize([float(v) for v in json.loads(hit["vector_json"])])
                if vec:
                    vectors[row["id"]] = vec
                    continue
            except Exception:
                pass
        missing.append((row, text))
    if not allow_network:
        return vectors
    for offset in range(0, len(missing), 24):
        batch = missing[offset:offset + 24]
        try:
            batch_vectors = ollama.embed([text for _, text in batch])
        except (ollama.OllamaError, OSError, ConnectionError):
            # Offline operation must preserve existing memories and allow FTS-only retrieval.
            # Missing vectors are retried on the next successful embedding request.
            break
        for (row, text), vec in zip(batch, batch_vectors):
            vec = normalize(vec)
            vectors[row["id"]] = vec
            conn.execute(
                """INSERT INTO memory_embeddings (node_id, model, text_hash, vector_json, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET model=excluded.model, text_hash=excluded.text_hash, vector_json=excluded.vector_json, updated_at=excluded.updated_at""",
                (row["id"], config.EMBED_MODEL, hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest(), json.dumps(vec), now_iso()),
            )
    return vectors


# ----------------------------------------------------------------------------------------
# Chat ingestion (visible-memory invariant)
# ----------------------------------------------------------------------------------------

TRANSIENT_PHRASES = [
    "do you have access", "what do you have access", "what emails", "which emails", "are you connected", "status report",
    "are you up and running", "what can you do", "what files can you", "can you access", "did you actually", "be honest",
]
EXPLICIT_PHRASES = ["remember that", "from now on", "going forward", "i prefer", "my goal", "my goals", "my priority", "my priorities"]
DURABLE_PHRASES = ["deadline", "due date", "meeting", "appointment", "project", "assignment", "exam", "i need to", "i plan to", "i am working on", "i'm working on"]


def extract_terms(text: str, limit: int = 7) -> list[str]:
    terms: list[str] = []
    for pat in [r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){0,2}\b",
                r"\b(?:email|calendar|project|deadline|priority|exam|meeting|follow[- ]?up|assignment|startup|music|sports|finance|class)\b"]:
        for m in re.findall(pat, text, flags=re.I):
            clean = " ".join(str(m).strip().split()).title()
            if len(clean) > 2 and clean.lower() not in {"the", "and", "you", "that", "this"} and clean not in terms:
                terms.append(clean[:70])
            if len(terms) >= limit:
                return terms
    return terms


def should_store_durable(content: str) -> bool:
    text = " ".join((content or "").strip().split())
    if len(text) < 24 or len(text) > 1800:
        return False
    lower = text.lower()
    if any(p in lower for p in TRANSIENT_PHRASES):
        return False
    if any(p in lower for p in EXPLICIT_PHRASES):
        return True
    if "?" in text:
        return False
    return any(p in lower for p in DURABLE_PHRASES)


def parent_for_text(content: str) -> str:
    lower = (content or "").lower()
    scores = {
        "seed-email-triage": sum(2 for w in ["email", "inbox", "sender", "reply", "receipt", "thread", "message", "gmail", "follow up", "follow-up"] if w in lower),
        "seed-calendar-events": sum(2 for w in ["calendar", "meeting", "event", "appointment", "schedule", "time block", "reservation", "flight", "interview"] if w in lower),
        "seed-project-priorities": sum(2 for w in ["project", "priority", "assignment", "exam", "homework", "task", "startup", "due", "deadline", "deliverable"] if w in lower),
        PERSONAL_CLOUD: sum(2 for w in ["goal", "habit", "background", "personal", "like", "dislike"] if w in lower),
    }
    if any(w in lower for w in ["i prefer", "from now on", "going forward", "remember that"]):
        scores[PERSONAL_CLOUD] += 6
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else PERSONAL_CLOUD


def memory_title(content: str) -> str:
    text = " ".join((content or "").strip().split())
    text = re.sub(r"^(remember that|from now on|going forward)[,:]?\s*", "", text, flags=re.I)
    if len(text) <= 74:
        return text
    first = re.split(r"[.;!?]", text)[0].strip()
    return first if 10 <= len(first) <= 74 else text[:71] + "..."


def related_links(conn, node_id: str, parent_id: str, tags: list[str]) -> None:
    tagset = {t.lower() for t in tags if t}
    linked = 0
    for row in rows(conn, "SELECT id, parent_id, tags_json FROM memory_nodes WHERE id != ? AND node_type='memory' ORDER BY updated_at DESC LIMIT 80", (node_id,)):
        try:
            other = {str(t).lower() for t in json.loads(row["tags_json"] or "[]")}
        except Exception:
            other = set()
        shared = tagset & other
        same_cloud = row["parent_id"] == parent_id
        if not shared and not same_cloud:
            continue
        strength = min(0.88, 0.34 + len(shared) * 0.12 + (0.12 if same_cloud else 0))
        if strength < 0.46:
            continue
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'related_to', ?, ?, ?)",
                     (str(uuid.uuid4()), node_id, row["id"], strength, now_iso(), now_iso()))
        linked += 1
        if linked >= 6:
            break


def ingest_chat_turn(conn, role: str, content: str, source_message_id: str | None = None, created_at: str | None = None) -> str | None:
    """Mirror a chat turn into the visible memory graph. Returns the node id (or None if skipped)."""
    if role not in {"user", "assistant"}:
        return None
    raw = (content or "").strip()
    if not raw:
        return None
    if source_message_id:
        existing = one(conn, "SELECT id FROM memory_nodes WHERE source_message_id=? LIMIT 1", (source_message_id,))
        if existing:
            return existing["id"]
    durable = role == "user" and should_store_durable(raw)
    parent_id = parent_for_text(raw) if durable else CONVERSATION_CLOUD
    memory_kind = "durable" if durable else "conversation"
    tags = extract_terms(raw, limit=9)
    for tag in [memory_kind, role]:
        if tag not in [str(t).lower() for t in tags]:
            tags.append(tag)
    node_id = str(uuid.uuid4())
    base_title = memory_title(raw)
    title = base_title if durable else f"{'You' if role == 'user' else 'PODA'}: {base_title}"
    reference_triggers = ", ".join(tags[:10]) if durable else "conversation context, recent discussion"
    timestamp = created_at or now_iso()
    importance = 0.60 if durable else (0.30 if role == "user" else 0.20)
    confidence = 0.85 if durable else (0.7 if role == "user" else 0.4)
    conn.execute(
        """INSERT INTO memory_nodes (id, parent_id, title, node_type, summary, content, reference_triggers, tags_json, x, y, z, pinned, importance,
           memory_kind, source_message_id, speaker, confidence, created_at, updated_at)
           VALUES (?, ?, ?, 'memory', ?, ?, ?, ?, NULL, NULL, NULL, 0, ?, ?, ?, ?, ?, ?, ?)""",
        (node_id, parent_id, title[:180], " ".join(raw.split())[:420], raw[:12000], reference_triggers, json.dumps(tags[:20]), importance,
         memory_kind, source_message_id, role, confidence, timestamp, timestamp),
    )
    conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', ?, ?, ?)",
                 (str(uuid.uuid4()), parent_id, node_id, 0.72 if durable else 0.52, timestamp, timestamp))
    if durable:
        related_links(conn, node_id, parent_id, tags)
    return node_id


PAIR_WINDOW_SECONDS = 15 * 60


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].strip()
    return (first if 8 <= len(first) <= limit else text[:limit]).strip()


def _parse_iso(value: str | None):
    from datetime import datetime
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except Exception:
        return None


def find_pair_candidate(conn, session_id: str | None, created_at: str) -> dict[str, Any] | None:
    """Most recent unpaired visible user node for this session, else the most recent one within the pair window."""
    base = """SELECT n.*, m.session_id AS msg_session, m.created_at AS msg_created FROM memory_nodes n JOIN messages m ON m.id = n.source_message_id
              WHERE n.node_type='memory' AND m.role='user' AND n.response_message_id IS NULL AND m.created_at <= ?"""
    if session_id:
        hit = one(conn, base + " AND m.session_id=? ORDER BY m.created_at DESC LIMIT 1", (created_at, session_id))
        if hit:
            return hit
    hit = one(conn, base + " ORDER BY m.created_at DESC LIMIT 1", (created_at,))
    if not hit:
        return None
    a, b = _parse_iso(hit["msg_created"]), _parse_iso(created_at)
    if a and b and abs((b - a).total_seconds()) <= PAIR_WINDOW_SECONDS:
        return hit
    return None


def apply_pair(conn, node: dict[str, Any], response_message_id: str, assistant_text: str) -> None:
    """Attach an assistant reply to a user node, turning it into a sequential question→response pair."""
    user_text = node.get("user_text") or node.get("content") or ""
    reply = (assistant_text or "").strip()
    kind = node.get("memory_kind") or "conversation"
    new_kind = "durable" if kind == "durable" else "exchange"
    base_title = memory_title(user_text)
    title = node.get("title") if kind == "durable" else f"Q: {base_title}"
    content = f"You: {user_text.strip()}\n\nPODA: {reply}"
    summary = (" ".join(user_text.split())[:300] + (" — PODA: " + _first_sentence(reply) if reply else "")).strip()[:420]
    try:
        tags = json.loads(node.get("tags_json") or "[]")
    except Exception:
        tags = []
    tags = [t for t in tags if str(t).lower() not in {"user", "assistant", "conversation"}]
    for tag in [new_kind if new_kind != "durable" else "durable", "exchange"]:
        if tag not in [str(t).lower() for t in tags]:
            tags.append(tag)
    conn.execute("""UPDATE memory_nodes SET response_message_id=?, user_text=?, assistant_text=?, content=?, summary=?, title=?, memory_kind=?, speaker='exchange',
                    tags_json=?, updated_at=? WHERE id=?""",
                 (response_message_id, user_text, reply[:12000], content[:24000], summary, (title or base_title)[:180], new_kind, json.dumps(tags[:20]), now_iso(), node["id"]))
    conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node["id"],))
    if node.get("surface_id"):
        conn.execute("UPDATE memory_nodes SET stale=1 WHERE id=?", (node["surface_id"],))


def save_message(role: str, content: str, model: str | None = None, session_id: str | None = None, provenance: str = "chat",
                 metadata: dict[str, Any] | None = None) -> str:
    conn = connect()
    try:
        message_id = str(uuid.uuid4())
        created_at = now_iso()
        conn.execute("INSERT INTO messages (id, role, content, model, created_at, session_id, provenance, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (message_id, role, content, model, created_at, session_id, provenance, json.dumps(metadata) if metadata else None))
        node_id = None
        if role == "assistant" and (content or "").strip():
            candidate = find_pair_candidate(conn, session_id, created_at)
            if candidate:
                apply_pair(conn, candidate, message_id, content)
                node_id = candidate["id"]
        if node_id is None:
            node_id = ingest_chat_turn(conn, role, content, source_message_id=message_id, created_at=created_at)
        conn.commit()
        if node_id and role == "assistant":
            # Surface assignment needs an embedding; it must never break message persistence.
            try:
                from . import surface
                surface.assign_or_create(conn, node_id)
                conn.commit()
            except Exception:
                conn.rollback()
        return message_id
    finally:
        conn.close()


def pair_history(conn) -> dict[str, int]:
    """Idempotent migration: fold each assistant reply into the preceding user node, deleting the standalone reply node."""
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_nodes_response_message ON memory_nodes(response_message_id) WHERE response_message_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_surface ON memory_nodes(surface_id)")
    paired = removed = 0
    msgs = rows(conn, "SELECT id, role, content, created_at, session_id FROM messages ORDER BY created_at ASC, rowid ASC")
    pending: dict[str, Any] | None = None
    for msg in msgs:
        if msg["role"] == "user":
            node = one(conn, "SELECT * FROM memory_nodes WHERE source_message_id=? AND node_type='memory'", (msg["id"],))
            pending = {"node": node, "msg": msg} if node and not node.get("response_message_id") else None
            continue
        if msg["role"] != "assistant":
            continue
        if one(conn, "SELECT id FROM memory_nodes WHERE response_message_id=?", (msg["id"],)):
            pending = None
            continue
        if not pending:
            continue
        a, b = _parse_iso(pending["msg"]["created_at"]), _parse_iso(msg["created_at"])
        same_session = pending["msg"].get("session_id") and pending["msg"].get("session_id") == msg.get("session_id")
        if not same_session and not (a and b and abs((b - a).total_seconds()) <= PAIR_WINDOW_SECONDS):
            pending = None
            continue
        standalone = one(conn, "SELECT id FROM memory_nodes WHERE source_message_id=?", (msg["id"],))
        apply_pair(conn, pending["node"], msg["id"], msg["content"])
        paired += 1
        if standalone:
            conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (standalone["id"], standalone["id"]))
            conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (standalone["id"],))
            conn.execute("DELETE FROM memory_nodes WHERE id=?", (standalone["id"],))
            removed += 1
        pending = None
    return {"paired": paired, "standalone_removed": removed}


# ----------------------------------------------------------------------------------------
# Seed + reconciliation (idempotent, runs at startup)
# ----------------------------------------------------------------------------------------

def ensure_seed(conn) -> None:
    now = now_iso()
    conn.execute(
        """INSERT OR IGNORE INTO memory_nodes (id, parent_id, title, node_type, summary, content, tags_json, x, y, z, pinned, importance, created_at, updated_at)
           VALUES (?, NULL, 'PODA Personal Organization', 'root', 'Root node for personal organization, priorities, email, calendar, and projects.', '', ?, 0, 0, 0, 1, 0.95, ?, ?)""",
        (ROOT_ID, json.dumps(["poda", "root"]), now, now),
    )
    for title, summary, tags, x, y, z in SEED_DOMAINS:
        nid = "seed-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        conn.execute(
            """INSERT OR IGNORE INTO memory_nodes (id, parent_id, title, node_type, summary, content, tags_json, x, y, z, pinned, importance, created_at, updated_at)
               VALUES (?, ?, ?, 'domain', ?, ?, ?, ?, ?, ?, 1, 0.82, ?, ?)""",
            (nid, ROOT_ID, title, summary, summary, json.dumps(tags), x, y, z, now, now),
        )
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.85, ?, ?)",
                     ("edge-root-" + nid, ROOT_ID, nid, now, now))
    for pattern in ["%do you have access%", "%what emails%", "%status report%", "%are you up and running%", "%are you connected%", "%can you access%"]:
        conn.execute("UPDATE memory_nodes SET node_type='transient', parent_id=NULL, updated_at=? WHERE node_type='memory' AND parent_id=? AND lower(content) LIKE ?", (now, ROOT_ID, pattern))
    conn.execute("UPDATE memory_nodes SET parent_id=?, updated_at=? WHERE node_type='memory' AND parent_id=?", (PERSONAL_CLOUD, now, ROOT_ID))
    for row in rows(conn, "SELECT id FROM memory_nodes WHERE node_type='memory' AND parent_id=?", (PERSONAL_CLOUD,)):
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.72, ?, ?)",
                     (f"migrated-contains-{row['id']}", PERSONAL_CLOUD, row["id"], now, now))
    # Mirror every historical chat turn that lacks a visible node.
    for msg in rows(conn, "SELECT id, role, content, model, created_at FROM messages ORDER BY created_at ASC"):
        if one(conn, "SELECT id FROM memory_nodes WHERE source_message_id=? LIMIT 1", (msg["id"],)):
            continue
        exact = None
        if msg["role"] == "user":
            exact = one(conn, "SELECT id FROM memory_nodes WHERE node_type='memory' AND source_message_id IS NULL AND content=? ORDER BY created_at ASC LIMIT 1", (msg["content"],))
        if exact:
            conn.execute("UPDATE memory_nodes SET source_message_id=?, speaker='user', memory_kind=COALESCE(NULLIF(memory_kind,''),'durable'), updated_at=? WHERE id=?", (msg["id"], now, exact["id"]))
            continue
        ingest_chat_turn(conn, msg["role"], msg["content"], source_message_id=msg["id"], created_at=msg["created_at"])
    pair_history(conn)
    for source, target, relation, strength in [
        ("seed-email-triage", "seed-calendar-events", "scheduling_signal", 0.72),
        ("seed-email-triage", "seed-project-priorities", "work_signal", 0.66),
        ("seed-calendar-events", "seed-project-priorities", "time_constraint", 0.78),
        ("seed-project-priorities", PERSONAL_CLOUD, "guided_by", 0.61),
    ]:
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (f"seed-cross-{source}-{target}", source, target, relation, strength, now, now))
    try:
        from . import surface
        surface.schedule_startup_rebuild()
    except Exception:
        pass


def node_tier(node: dict[str, Any]) -> str:
    if node.get("node_type") == "surface":
        return "surface"
    if node.get("node_type") != "memory":
        return node.get("node_type") or "memory"
    return "durable" if (node.get("memory_kind") or "durable") == "durable" else "in_depth"


def node_to_api(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    try:
        out["tags"] = json.loads(out.pop("tags_json") or "[]")
    except Exception:
        out["tags"] = []
    out["pinned"] = bool(out.get("pinned"))
    out["tier"] = node_tier(out)
    for key in ("user_text", "assistant_text", "response_message_id", "surface_id"):
        out.setdefault(key, None)
    out["member_count"] = int(out.get("member_count") or 0)
    out["stale"] = bool(out.get("stale"))
    out["user_edited"] = bool(out.get("user_edited"))
    return out


def list_graph(conn, include_transient: bool = False) -> dict[str, Any]:
    where = "" if include_transient else "WHERE node_type != 'transient'"
    nodes = [node_to_api(n) for n in rows(conn, f"SELECT * FROM memory_nodes {where} ORDER BY importance DESC, updated_at DESC")]
    visible = {n["id"] for n in nodes}
    links = [l for l in rows(conn, "SELECT * FROM memory_links ORDER BY strength DESC") if l["source_id"] in visible and l["target_id"] in visible]
    return {"nodes": nodes, "links": links}


def recent_context(limit: int = 10, session_id: str | None = None) -> str:
    """Recent chat context comes only from visible memory nodes, never a hidden history store.

    With a session_id only that conversation's turns are included (each chat starts fresh); everything older is
    reachable solely through semantic recall, so the second brain decides what from the past is relevant."""
    conn = connect()
    try:
        cols = "n.speaker, n.content, n.memory_kind, n.source_message_id, n.response_message_id, n.user_text, n.assistant_text"
        if session_id:
            recent = rows(conn, f"""SELECT {cols} FROM memory_nodes n JOIN messages m ON m.id = n.source_message_id
                                   WHERE n.node_type='memory' AND n.source_message_id IS NOT NULL AND m.session_id=?
                                   ORDER BY m.created_at DESC LIMIT ?""", (session_id, limit))
        else:
            recent = rows(conn, f"SELECT {cols} FROM memory_nodes n WHERE n.node_type='memory' AND n.source_message_id IS NOT NULL ORDER BY n.created_at DESC LIMIT ?", (limit,))
    finally:
        conn.close()
    lines = []
    for r in reversed(recent):
        if r.get("response_message_id"):
            lines.append(f"user: {(r.get('user_text') or '')[:420]}")
            lines.append(f"assistant (prior PODA response; not authoritative): {(r.get('assistant_text') or '')[:420]}")
            continue
        speaker = r["speaker"] or "user"
        prefix = "assistant (prior PODA response; not authoritative)" if speaker == "assistant" else "user"
        lines.append(f"{prefix}: {(r['content'] or '')[:420]}")
    return "\n".join(lines) or "[none]"


def recent_messages(limit: int = 12) -> list[dict[str, Any]]:
    """Chat history for the UI. Pair nodes expand back into their two turns so the transcript stays sequential."""
    conn = connect()
    try:
        recent = rows(conn, """SELECT n.id AS node_id, n.speaker, n.content, n.source_message_id, n.response_message_id, n.user_text, n.assistant_text, n.created_at,
                                      (SELECT created_at FROM messages WHERE id = n.response_message_id) AS response_created_at
                               FROM memory_nodes n WHERE n.node_type='memory' AND n.source_message_id IS NOT NULL ORDER BY n.created_at DESC LIMIT ?""", (limit,))
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in reversed(recent):
        if r.get("response_message_id"):
            out.append({"speaker": "user", "content": r.get("user_text") or "", "source_message_id": r["source_message_id"], "created_at": r["created_at"], "node_id": r["node_id"]})
            out.append({"speaker": "assistant", "content": r.get("assistant_text") or "", "source_message_id": r["response_message_id"],
                        "created_at": r.get("response_created_at") or r["created_at"], "node_id": r["node_id"]})
        else:
            out.append({"speaker": r["speaker"], "content": r["content"], "source_message_id": r["source_message_id"], "created_at": r["created_at"], "node_id": r["node_id"]})
    return out
