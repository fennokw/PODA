"""Surface memory: miniature clouds of distilled core meaning sitting above the in-depth exchange nodes.

Every in-depth node (question→response pair or durable fact) belongs to at most one surface (`surface_id`
plus a `distills` link for metadata pairing). Surfaces have their own embedding, so retrieval can look at
core meanings first and only dive into full exchanges when needed.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any

import numpy as np

from ..runtime import ollama, models
from ..runtime.db import connect, now_iso, rows, one
from . import store

SURFACE_TYPE = "surface"
_STATUS: dict[str, Any] = {"running": False, "last_run": None, "last_result": None, "error": None}
_LOCK = threading.Lock()


def _setting(conn, key: str, default: float) -> float:
    row = one(conn, "SELECT value FROM settings WHERE key=?", (key,))
    try:
        return float(row["value"]) if row and row.get("value") not in (None, "") else default
    except Exception:
        return default


def join_threshold(conn) -> float:
    return _setting(conn, "surface_join_threshold", 0.84)


def join_threshold_value() -> float:
    conn = connect()
    try:
        return join_threshold(conn)
    finally:
        conn.close()


def radius_for(member_count: int) -> float:
    return max(8.0, min(26.0, 6.0 + 2.2 * math.sqrt(max(0, member_count))))


def _clean_title(title: str) -> str:
    return re.sub(r"^(Q:|You:|PODA:)\s*", "", title or "").strip()[:120] or "Untitled memory"


def seed_title(node: dict[str, Any]) -> str:
    """A singleton surface is named after the user's actual question, not a clipped first clause like 'Looks good'."""
    base = _clean_title(node.get("title") or "")
    text = " ".join((node.get("user_text") or node.get("content") or "").replace("You:", "", 1).split())
    if len(base) < 25 and len(text) > len(base):
        return (text[:72] + ("…" if len(text) > 72 else "")).strip()
    return base


def _surface_rows(conn) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM memory_nodes WHERE node_type=?", (SURFACE_TYPE,))


def _members(conn, surface_id: str) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM memory_nodes WHERE node_type='memory' AND surface_id=? ORDER BY created_at ASC", (surface_id,))


def create_surface(conn, seed: dict[str, Any], stale: bool = True) -> str:
    """New surfaces start stale so even single-member surfaces get a distilled core meaning instead of a raw question title."""
    sid = str(uuid.uuid4())
    now = now_iso()
    conn.execute(
        """INSERT INTO memory_nodes (id, parent_id, title, node_type, summary, content, reference_triggers, tags_json, x, y, z, pinned, importance,
           memory_kind, speaker, confidence, member_count, stale, user_edited, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0.55, 'surface', 'surface', 1.0, 0, ?, 0, ?, ?)""",
        (sid, seed.get("parent_id") or store.CONVERSATION_CLOUD, seed_title(seed), SURFACE_TYPE,
         (seed.get("summary") or seed.get("content") or "")[:420], (seed.get("summary") or seed.get("content") or "")[:2000],
         seed.get("reference_triggers") or "", json.dumps(["surface"]), seed.get("x"), seed.get("y"), seed.get("z"), 1 if stale else 0, now, now),
    )
    conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.8, ?, ?)",
                 (str(uuid.uuid4()), seed.get("parent_id") or store.CONVERSATION_CLOUD, sid, now, now))
    return sid


def set_membership(conn, node_id: str, surface_id: str | None) -> None:
    """Move a node into `surface_id` (or out of any surface when None), keeping counts, links and staleness consistent."""
    node = one(conn, "SELECT id, surface_id FROM memory_nodes WHERE id=?", (node_id,))
    if not node:
        return
    old = node.get("surface_id")
    if old == surface_id:
        return
    now = now_iso()
    conn.execute("UPDATE memory_nodes SET surface_id=?, updated_at=? WHERE id=?", (surface_id, now, node_id))
    if old:
        conn.execute("DELETE FROM memory_links WHERE relation='distills' AND source_id=? AND target_id=?", (old, node_id))
        refresh_counts(conn, old)
    if surface_id:
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'distills', 0.9, ?, ?)",
                     (str(uuid.uuid4()), surface_id, node_id, now, now))
        refresh_counts(conn, surface_id)


def refresh_counts(conn, surface_id: str) -> None:
    count = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='memory' AND surface_id=?", (surface_id,)).fetchone()[0]
    conn.execute("UPDATE memory_nodes SET member_count=?, stale=CASE WHEN user_edited=1 THEN 0 WHEN ?>=2 THEN 1 ELSE stale END, updated_at=? WHERE id=?",
                 (count, count, now_iso(), surface_id))


def assign_or_create(conn, node_id: str, vectors: dict[str, list[float]] | None = None) -> str | None:
    """Attach an in-depth node to the most similar surface (same cloud first) or create a new surface seeded from it."""
    node = one(conn, "SELECT * FROM memory_nodes WHERE id=? AND node_type='memory'", (node_id,))
    if not node:
        return None
    if node.get("surface_id") and one(conn, "SELECT id FROM memory_nodes WHERE id=?", (node["surface_id"],)):
        return node["surface_id"]
    vectors = vectors or store.ensure_embeddings(conn)
    vec = vectors.get(node_id)
    if vec is None:
        return None
    threshold = join_threshold(conn)
    surfaces = [s for s in _surface_rows(conn) if s["id"] in vectors]
    best_id, best_cos = None, -2.0
    if surfaces:
        sims = [surface_similarity(conn, s["id"], vec, vectors) for s in surfaces]
        same = [(float(sims[i]), s["id"]) for i, s in enumerate(surfaces) if s.get("parent_id") == node.get("parent_id")]
        anywhere = [(float(sims[i]), s["id"]) for i, s in enumerate(surfaces)]
        for pool in (same, anywhere):
            if pool:
                cos, sid = max(pool)
                if cos >= threshold:
                    best_id, best_cos = sid, cos
                    break
    if best_id:
        set_membership(conn, node_id, best_id)
        return best_id
    sid = create_surface(conn, {"parent_id": node.get("parent_id"), "title": node.get("title"), "summary": node.get("summary"), "user_text": node.get("user_text"),
                                "content": node.get("content"), "reference_triggers": node.get("reference_triggers"),
                                "x": node.get("x"), "y": node.get("y"), "z": node.get("z")})
    set_membership(conn, node_id, sid)
    return sid


def surface_similarity(conn, surface_id: str, vec: list[float], vectors: dict[str, list[float]]) -> float:
    """Similarity of a candidate node to a surface = max(cosine to the surface's own vector, mean cosine to its members).

    Comparing against the members' actual content is what keeps genuinely related exchanges together; the distilled
    surface text alone is shorter and drifts lower in cosine space."""
    v = np.asarray(vec, dtype=float)
    scores = []
    if surface_id in vectors:
        scores.append(float(np.asarray(vectors[surface_id]) @ v))
    member_vecs = [vectors[m["id"]] for m in rows(conn, "SELECT id FROM memory_nodes WHERE surface_id=? AND node_type='memory'", (surface_id,)) if m["id"] in vectors]
    if member_vecs:
        scores.append(float((np.asarray(member_vecs, dtype=float) @ v).mean()))
    return max(scores) if scores else -1.0


def _member_digest(members: list[dict[str, Any]], cap: int = 12, chars: int = 300) -> str:
    lines = []
    for m in members[:cap]:
        text = (m.get("summary") or m.get("content") or "").replace("\n", " ")
        lines.append(f"- [{(m.get('created_at') or '')[:10]}] {text[:chars]}")
    return "\n".join(lines)


def _deterministic_distill(members: list[dict[str, Any]]) -> dict[str, Any]:
    first = members[0] if members else {}
    joined = " ".join((m.get("summary") or m.get("content") or "")[:160] for m in members[:6])
    return {"title": seed_title(first), "core": " ".join(joined.split())[:600], "triggers": first.get("reference_triggers") or ""}


def distill_text(members: list[dict[str, Any]], model: str | None) -> tuple[dict[str, Any], str]:
    """Return ({title, core, triggers}, method). Uses the fast local model; deterministic fallback when unavailable."""
    if not members:
        return {"title": "Empty surface", "core": "", "triggers": ""}, "empty"
    if not model:  # singletons are distilled too: a core meaning beats a raw question title
        return _deterministic_distill(members), "deterministic"
    prompt = ("You compress related personal-assistant conversation exchanges into ONE core memory. Return ONLY JSON: "
              '{"title": "<= 60 chars", "core": "<= 3 sentences stating the shared meaning, decisions, preferences or facts, no detail", '
              '"triggers": "comma separated cues that should recall this"}. Do not invent facts; if the exchanges disagree, say so briefly.\n\nEXCHANGES:\n' + _member_digest(members))
    try:
        resp = ollama.chat([{"role": "user", "content": prompt}], model, options={"temperature": 0.05, "num_ctx": 8192, "num_predict": 320},
                           think=False if model.startswith("qwen3") else None, timeout=120)
        text = (resp.get("message", {}).get("content") or "").strip()
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0)) if match else {}
        title = str(data.get("title") or "").strip()
        core = str(data.get("core") or "").strip()
        triggers = data.get("triggers") or ""
        if isinstance(triggers, list):
            triggers = ", ".join(str(t) for t in triggers)
        if title and core:
            return {"title": title[:120], "core": core[:1200], "triggers": str(triggers)[:600]}, f"model:{model}"
    except Exception:
        pass
    return _deterministic_distill(members), "deterministic-fallback"


def _fast_model() -> str | None:
    try:
        reg = models.registry()
        return reg["profiles"]["fast"]["model"] or reg["profiles"]["balanced"]["model"]
    except Exception:
        return None


def distill_surface(conn, surface_id: str, force: bool = False, model: str | None = None) -> dict[str, Any] | None:
    surface = one(conn, "SELECT * FROM memory_nodes WHERE id=? AND node_type=?", (surface_id, SURFACE_TYPE))
    if not surface:
        return None
    members = _members(conn, surface_id)
    if surface.get("user_edited") and not force:
        refresh_counts(conn, surface_id)
        conn.execute("UPDATE memory_nodes SET stale=0 WHERE id=?", (surface_id,))
        return one(conn, "SELECT * FROM memory_nodes WHERE id=?", (surface_id,))
    text, method = distill_text(members, model if model is not None else _fast_model())
    conn.execute("""UPDATE memory_nodes SET title=?, summary=?, content=?, reference_triggers=?, stale=0, member_count=?, updated_at=? WHERE id=?""",
                 (text["title"][:180], text["core"][:420], text["core"][:2000], text["triggers"][:600], len(members), now_iso(), surface_id))
    conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (surface_id,))
    update_confidence(conn, surface_id)
    out = one(conn, "SELECT * FROM memory_nodes WHERE id=?", (surface_id,)) or {}
    out["distill_method"] = method
    return out


def update_confidence(conn, surface_id: str, vectors: dict[str, list[float]] | None = None) -> float:
    """Confidence = mean cosine between the surface embedding and its members."""
    try:
        vectors = vectors or store.ensure_embeddings(conn)
    except Exception:
        return float((one(conn, "SELECT confidence FROM memory_nodes WHERE id=?", (surface_id,)) or {}).get("confidence") or 0.0)
    svec = vectors.get(surface_id)
    members = [m["id"] for m in _members(conn, surface_id) if m["id"] in vectors]
    if not svec or not members:
        conf = 1.0 if not members else 0.5
    else:
        matrix = np.asarray([vectors[m] for m in members], dtype=float)
        conf = float(np.clip((matrix @ np.asarray(svec)).mean(), -1.0, 1.0))
    conn.execute("UPDATE memory_nodes SET confidence=? WHERE id=?", (round(conf, 4), surface_id))
    return conf


def distill_stale(conn, limit: int = 5) -> list[dict[str, Any]]:
    model = _fast_model()
    out = []
    for s in rows(conn, "SELECT id FROM memory_nodes WHERE node_type=? AND stale=1 ORDER BY updated_at ASC LIMIT ?", (SURFACE_TYPE, max(1, limit))):
        result = distill_surface(conn, s["id"], model=model)
        if result:
            out.append(result)
    return out


def status(conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or connect()
    try:
        surfaces = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type=?", (SURFACE_TYPE,)).fetchone()[0]
        in_depth = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='memory'").fetchone()[0]
        unassigned = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='memory' AND (surface_id IS NULL OR surface_id NOT IN (SELECT id FROM memory_nodes WHERE node_type=?))", (SURFACE_TYPE,)).fetchone()[0]
        stale = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type=? AND stale=1", (SURFACE_TYPE,)).fetchone()[0]
        threshold = join_threshold(conn)
    finally:
        if own:
            conn.close()
    return {"surfaces": surfaces, "in_depth": in_depth, "unassigned": unassigned, "stale": stale, "join_threshold": threshold, **_STATUS}


def rebuild(conn, mode: str = "incremental", distill_limit: int = 50) -> dict[str, Any]:
    """incremental: assign unassigned nodes, distill stale surfaces. full: re-cluster everything except user-edited surfaces."""
    started = time.perf_counter()
    vectors = store.ensure_embeddings(conn)
    created = joined = 0
    if mode == "full":
        protected = {s["id"] for s in _surface_rows(conn) if s.get("user_edited")}
        for s in _surface_rows(conn):
            if s["id"] in protected:
                continue
            conn.execute("UPDATE memory_nodes SET surface_id=NULL WHERE surface_id=?", (s["id"],))
            conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (s["id"], s["id"]))
            conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (s["id"],))
            conn.execute("DELETE FROM memory_nodes WHERE id=?", (s["id"],))
        threshold = join_threshold(conn)
        clusters: list[dict[str, Any]] = []  # {"ids": [...], "sum": np.array, "parent": str}
        for node in rows(conn, "SELECT * FROM memory_nodes WHERE node_type='memory' AND surface_id IS NULL ORDER BY created_at ASC"):
            vec = vectors.get(node["id"])
            if vec is None:
                continue
            v = np.asarray(vec, dtype=float)
            best, best_cos = None, -2.0
            for c in clusters:
                centroid = c["sum"] / (np.linalg.norm(c["sum"]) or 1.0)
                cos = float(centroid @ v)
                if cos > best_cos and c["parent"] == node.get("parent_id"):
                    best, best_cos = c, cos
            if best is None or best_cos < threshold:
                for c in clusters:  # second pass: any cloud
                    centroid = c["sum"] / (np.linalg.norm(c["sum"]) or 1.0)
                    cos = float(centroid @ v)
                    if cos > best_cos:
                        best, best_cos = c, cos
            if best is not None and best_cos >= threshold:
                best["ids"].append(node["id"]); best["sum"] = best["sum"] + v; joined += 1
            else:
                clusters.append({"ids": [node["id"]], "sum": v.copy(), "parent": node.get("parent_id"), "seed": node}); created += 1
        for c in clusters:
            sid = create_surface(conn, {"parent_id": c["parent"], "title": c["seed"].get("title"), "summary": c["seed"].get("summary"), "content": c["seed"].get("content"), "user_text": c["seed"].get("user_text"),
                                        "reference_triggers": c["seed"].get("reference_triggers"), "x": c["seed"].get("x"), "y": c["seed"].get("y"), "z": c["seed"].get("z")}, stale=True)
            for nid in c["ids"]:
                set_membership(conn, nid, sid)
    else:
        for node in rows(conn, "SELECT id FROM memory_nodes WHERE node_type='memory' AND (surface_id IS NULL OR surface_id NOT IN (SELECT id FROM memory_nodes WHERE node_type=?)) ORDER BY created_at ASC", (SURFACE_TYPE,)):
            before = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type=?", (SURFACE_TYPE,)).fetchone()[0]
            if assign_or_create(conn, node["id"], vectors):
                after = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type=?", (SURFACE_TYPE,)).fetchone()[0]
                if after > before:
                    created += 1
                    vectors = store.ensure_embeddings(conn)  # new surface needs a vector for later comparisons
                else:
                    joined += 1
    distilled = distill_stale(conn, limit=distill_limit)
    vectors = store.ensure_embeddings(conn)
    for s in _surface_rows(conn):
        refresh_counts(conn, s["id"])
        conn.execute("UPDATE memory_nodes SET stale=0 WHERE id=? AND member_count<2", (s["id"],))
        update_confidence(conn, s["id"], vectors)
    conn.execute("DELETE FROM memory_nodes WHERE node_type=? AND member_count=0 AND user_edited=0", (SURFACE_TYPE,))
    return {"mode": mode, "created": created, "joined": joined, "distilled": len(distilled), "seconds": round(time.perf_counter() - started, 2), **status(conn)}


def _background_rebuild() -> None:
    with _LOCK:
        _STATUS.update({"running": True, "error": None})
    conn = connect()
    try:
        result = rebuild(conn, "incremental")
        conn.commit()
        with _LOCK:
            _STATUS.update({"last_result": result})
    except Exception as exc:
        conn.rollback()
        with _LOCK:
            _STATUS.update({"error": str(exc)})
    finally:
        conn.close()
        with _LOCK:
            _STATUS.update({"running": False, "last_run": now_iso()})


def schedule_startup_rebuild() -> bool:
    """Start an incremental rebuild in the background when Ollama is reachable. Disabled by PODA_BACKGROUND_SURFACES=0."""
    if os.getenv("PODA_BACKGROUND_SURFACES", "1") == "0" or _STATUS["running"]:
        return False
    try:
        if not ollama.status().get("reachable"):
            return False
    except Exception:
        return False
    threading.Thread(target=_background_rebuild, name="poda-surface-rebuild", daemon=True).start()
    return True


def dive(surface_id: str | None = None, query: str | None = None, limit: int = 8, conn=None) -> dict[str, Any]:
    """Read the in-depth members (full question→response pairs) of a surface, or of the best surface for a query."""
    own = conn is None
    conn = conn or connect()
    try:
        target = None
        if surface_id:
            target = one(conn, "SELECT * FROM memory_nodes WHERE id=? AND node_type=?", (surface_id, SURFACE_TYPE))
        if not target and query:
            from . import retrieval
            recalled = retrieval.recall(query, limit=6, depth="surface", conn=conn)
            best = next((r for r in recalled.get("results", []) if r.get("tier") == "surface"), None)
            if best:
                target = one(conn, "SELECT * FROM memory_nodes WHERE id=?", (best["id"],))
        if not target:
            return {"surface": None, "members": [], "note": "no matching surface memory"}
        members = _members(conn, target["id"])[: max(1, min(int(limit), 8))]
        return {"surface": {"id": target["id"], "title": target["title"], "core": target.get("content") or target.get("summary"), "member_count": target.get("member_count")},
                "members": [{"id": m["id"], "title": m["title"], "created_at": m["created_at"], "user_text": m.get("user_text") or m.get("content"),
                             "assistant_text": m.get("assistant_text"), "memory_kind": m.get("memory_kind")} for m in members]}
    finally:
        if own:
            conn.close()
