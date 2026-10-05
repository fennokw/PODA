"""Hybrid retrieval: FTS5/BM25 lexical + embedding cosine + committed-geometry propagation + trigger/provenance/recency reranking.

Every result carries its ranking factors so the UI can answer "Why was this recalled?" without exposing
hidden model reasoning. Default blend keeps the v0.3.11 baseline of 72% text / 28% committed-spatial.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from ..runtime import config, ollama
from ..runtime.db import connect, rows, one
from .store import ensure_embeddings, normalize, cosine
from .layout import geometry_scale, geometry_cosine


STOP_WORDS = {"the", "and", "you", "that", "this", "with", "what", "about", "from", "when", "where", "which", "who", "how", "why", "is", "are", "was",
              "were", "be", "been", "do", "does", "did", "my", "me", "i", "it", "its", "to", "of", "in", "on", "at", "for", "an", "a", "or", "if", "so", "we",
              "us", "our", "your", "can", "could", "would", "should", "will", "have", "has", "had", "not", "no", "yes", "there", "here", "then", "than", "also", "just", "please"}


def _fts_query(query: str) -> str:
    tokens = [t for t in re.findall(r"[A-Za-z0-9_']{2,}", query.lower()) if t not in STOP_WORDS]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def lexical_scores(conn, query: str, limit: int = 60) -> dict[str, float]:
    match = _fts_query(query)
    if not match:
        return {}
    try:
        hits = rows(conn, """SELECT f.node_id AS node_id, bm25(memory_fts, 3.0, 1.5, 1.0, 2.5, 1.0) AS rank FROM memory_fts f
                             JOIN memory_nodes n ON n.id = f.node_id WHERE memory_fts MATCH ? AND n.node_type='memory' ORDER BY rank LIMIT ?""", (match, limit))
    except Exception:
        return {}
    if not hits:
        return {}
    # bm25() returns negative numbers (lower is better). Normalize to 0..1.
    ranks = [-float(h["rank"]) for h in hits]
    lo, hi = min(ranks), max(ranks)
    span = (hi - lo) or 1.0
    return {h["node_id"]: 0.35 + 0.65 * ((-float(h["rank"]) - lo) / span) for h in hits}


def _trigger_boost(query_lower: str, triggers: str) -> float:
    if not triggers:
        return 0.0
    hits = 0
    for raw in re.split(r"[,\n;]+", triggers):
        term = raw.strip().lower()
        if len(term) >= 3 and term in query_lower and term not in {"conversation context", "recent discussion"}:
            hits += 1
    return min(0.25, hits * 0.08)


def _recency(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        return math.exp(-days / 45.0)
    except Exception:
        return 0.0


def _provenance(kind: str, speaker: str, paired: bool) -> tuple[str, float]:
    if kind == "surface":
        return "surface memory — distilled core meaning of related exchanges", 0.95
    if kind == "exchange" or (paired and kind != "durable"):
        return "user question with PODA's reply — reply is contextual, not authoritative", 0.84
    if kind == "conversation" and speaker == "assistant":
        return "prior PODA response — contextual, not authoritative", 0.78
    if kind == "conversation":
        return "user conversation", 0.88
    if kind == "summary":
        return "source-linked session summary", 0.92
    return ("durable user memory (with PODA's reply attached, reply not authoritative)" if paired else "durable user memory"), 1.0


def _surface_propagation(direct: dict[str, float], vectors: dict[str, list[float]], surface_ids: list[str], neighbor_map: dict[str, list[str]], damping: float = 0.85) -> dict[str, float]:
    """S'_j = max(S_j, damping · max_k S_k · cos(j,k)) over each surface's top neighbours (one hop)."""
    out: dict[str, float] = {}
    for sid in surface_ids:
        own = direct.get(sid, 0.0)
        best = own
        for other in neighbor_map.get(sid, []):
            if other == sid or other not in direct:
                continue
            sim = (cosine(vectors[sid], vectors[other]) + 1.0) / 2.0 if sid in vectors and other in vectors else 0.0
            best = max(best, damping * direct[other] * sim)
        out[sid] = best
    return out


def _top_neighbors(vectors: dict[str, list[float]], ids: list[str], k: int = 8) -> dict[str, list[str]]:
    if len(ids) < 2:
        return {i: [] for i in ids}
    import numpy as np
    matrix = np.asarray([vectors[i] for i in ids], dtype=float)
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -2.0)
    kk = min(k, len(ids) - 1)
    return {ids[i]: [ids[int(j)] for j in np.argpartition(-sims[i], kk)[:kk]] for i in range(len(ids))}


def recall(query: str, limit: int = 18, conn=None, depth: str = "auto", thinking_level: str = "balanced") -> dict[str, Any]:
    """Tiered hybrid retrieval. Never raises; degrades to lexical-only when embeddings fail.

    depth: 'surface' → surfaces + durable facts; 'auto' → surface tier plus member titles for the top-2 surfaces;
    'deep' → surfaces plus full in-depth members of the top-3 surfaces (and any member that beats the 3rd surface).
    Member text score = unified context: w_self·cos(query, member) + w_surface·S'(its surface).
    """
    depth = "deep" if (depth == "deep" or (depth == "auto" and thinking_level == "deep")) else ("surface" if depth == "surface" else "auto")
    own = conn is None
    if own:
        try:
            from .embedding_jobs import refresh_pending
            refresh_pending(limit=8)
        except Exception:
            pass  # Offline embedding should never prevent lexical memory retrieval.
    conn = conn or connect()
    try:
        def setting(key: str, default: float) -> float:
            row = one(conn, "SELECT value FROM settings WHERE key=?", (key,))
            try:
                return float(row["value"]) if row and row.get("value") not in (None, "") else default
            except Exception:
                return default
        weight_text, weight_geo = setting("retrieval_text_weight", 0.72), setting("retrieval_geometry_weight", 0.28)
        w_self, w_surface = setting("retrieval_unified_self_weight", 0.6), setting("retrieval_unified_surface_weight", 0.4)
        node_rows = {r["id"]: r for r in rows(conn, "SELECT * FROM memory_nodes WHERE node_type IN ('memory','surface')")}
        if not node_rows:
            return {"query": query, "results": [], "mode": "empty", "depth_used": depth, "surfaces": []}
        surface_ids = [i for i, r in node_rows.items() if r["node_type"] == "surface"]
        member_ids = [i for i, r in node_rows.items() if r["node_type"] == "memory"]
        lexical = lexical_scores(conn, query)
        mode = "hybrid"
        vectors: dict[str, list[float]] = {}
        direct: dict[str, float] = {}
        try:
            vectors = ensure_embeddings(conn)
            if own:
                conn.commit()
            qvec = normalize(ollama.embed([query[:7000]])[0])
            direct = {nid: (cosine(qvec, vec) + 1.0) / 2.0 for nid, vec in vectors.items() if nid in node_rows}
        except Exception as exc:
            mode = f"lexical_only ({exc.__class__.__name__})"
        # Surface graph propagation (unified context across surfaces).
        neighbor_map: dict[str, list[str]] = {}
        if direct and surface_ids:
            links = rows(conn, "SELECT source_id, target_id FROM memory_links WHERE relation='semantic_neighbor'")
            sset = set(surface_ids)
            for l in links:
                if l["source_id"] in sset and l["target_id"] in sset:
                    neighbor_map.setdefault(l["source_id"], []).append(l["target_id"])
            if not neighbor_map:
                neighbor_map = _top_neighbors(vectors, [i for i in surface_ids if i in vectors])
        surface_prop = _surface_propagation(direct, vectors, [i for i in surface_ids if i in direct], neighbor_map) if direct else {}
        in_depth_rows = {i: node_rows[i] for i in member_ids}
        anchors = sorted(((i, direct[i]) for i in member_ids if i in direct), key=lambda kv: kv[1], reverse=True)[:6]
        scale = geometry_scale(in_depth_rows) if anchors else 90.0
        query_lower = query.lower()
        # Geometry affinity is only defined for nodes the layout has placed. Freshly ingested nodes (no XYZ yet) get the
        # mean geometry of placed nodes so they are neither punished nor favoured by a term they cannot have.
        geo_scores: dict[str, float] = {}
        if anchors:
            for nid, row in node_rows.items():
                if row.get("x") is None:
                    continue
                weighted = total = 0.0
                for anchor_id, anchor_score in anchors:
                    affinity = 1.0 if anchor_id == nid else (geometry_cosine(row, node_rows[anchor_id], scale) + 1.0) / 2.0
                    w = max(0.05, anchor_score)
                    weighted += affinity * w
                    total += w
                geo_scores[nid] = weighted / total if total else 0.0
        geo_default = (sum(geo_scores.values()) / len(geo_scores)) if geo_scores else 0.0
        scored: dict[str, tuple[float, dict[str, Any]]] = {}
        for nid, row in node_rows.items():
            is_surface = row["node_type"] == "surface"
            self_cos = direct.get(nid, 0.0)
            sid = row.get("surface_id") if not is_surface else nid
            surf_score = surface_prop.get(sid, 0.0) if sid else 0.0
            if is_surface:
                text_cos = surf_score if direct else 0.0
                unified = text_cos
            elif direct and sid and sid in surface_prop:
                unified = w_self * self_cos + w_surface * surf_score
                text_cos = unified
            else:
                unified = self_cos
                text_cos = self_cos
            lex = lexical.get(nid, 0.0)
            geo_estimated = nid not in geo_scores
            geo = geo_scores.get(nid, geo_default)
            text_signal = (min(1.15, text_cos + 0.25 * lex) if direct else lex)
            trigger = _trigger_boost(query_lower, row.get("reference_triggers") or "")
            kind = row.get("memory_kind") or ("surface" if is_surface else "durable")
            speaker = row.get("speaker") or ""
            provenance, provenance_weight = _provenance(kind, speaker, bool(row.get("response_message_id")))
            importance = float(row.get("importance") or 0.5)
            recency = _recency(row.get("created_at"))
            superseded = bool(row.get("superseded_by")) or bool(row.get("valid_until"))
            base = weight_text * text_signal + weight_geo * geo
            provenance_penalty = (1.0 - provenance_weight) * 0.25
            final = base + trigger + 0.04 * importance + 0.03 * recency - provenance_penalty
            if superseded:
                final -= 0.15
            if final <= 0.0:
                continue
            scored[nid] = (final, {"text_cosine": round(text_cos, 3), "self_cosine": round(self_cos, 3), "unified_context": round(unified, 3), "surface_score": round(surf_score, 3),
                                   "surface_id": sid, "lexical_bm25": round(lex, 3), "geometry_cosine": round(geo, 3), "trigger_boost": round(trigger, 3),
                                   "provenance_weight": provenance_weight, "provenance_penalty": round(provenance_penalty, 3), "importance": round(importance, 2),
                                   "recency": round(recency, 3), "superseded": superseded, "geometry_estimated": geo_estimated, "provenance": provenance})
        surfaces_ranked = sorted(((scored[i][0], i) for i in surface_ids if i in scored), reverse=True)
        members_ranked = sorted(((scored[i][0], i) for i in member_ids if i in scored), reverse=True)
        surface_set = set(surface_ids)
        third_surface = surfaces_ranked[2][0] if len(surfaces_ranked) >= 3 else (surfaces_ranked[-1][0] if surfaces_ranked else 0.0)
        top_surface_ids = [i for _, i in surfaces_ranked[:3]]
        chosen: list[str] = [i for _, i in surfaces_ranked[:max(4, limit // 2)]]
        for score, nid in members_ranked:
            row = node_rows[nid]
            kind = row.get("memory_kind") or "durable"
            has_surface = row.get("surface_id") in surface_set
            if kind == "durable":
                chosen.append(nid)
            elif depth == "deep" and (row.get("surface_id") in top_surface_ids or score >= third_surface):
                chosen.append(nid)
            elif not has_surface and (score >= third_surface or not surfaces_ranked):
                chosen.append(nid)  # not yet distilled into a surface; must remain reachable
        seen: set[str] = set()
        ordered = []
        for nid in chosen:
            if nid not in seen and nid in scored:
                seen.add(nid); ordered.append((scored[nid][0], nid))
        ordered.sort(reverse=True)
        members_cache: dict[str, list[dict[str, Any]]] = {}
        results = []
        for final, nid in ordered[:limit]:
            row = node_rows[nid]
            factors = dict(scored[nid][1])
            provenance = factors.pop("provenance")
            is_surface = row["node_type"] == "surface"
            kind = row.get("memory_kind") or ("surface" if is_surface else "durable")
            tier = "surface" if is_surface else ("durable" if kind == "durable" else "in_depth")
            item = {"id": nid, "title": row.get("title"), "summary": row.get("summary") or (row.get("content") or "")[:420], "parent_id": row.get("parent_id"),
                    "memory_kind": kind, "speaker": row.get("speaker") or "", "provenance": provenance, "tier": tier, "surface_id": row.get("surface_id") if not is_surface else None,
                    "source_message_id": row.get("source_message_id"), "response_message_id": row.get("response_message_id"), "created_at": row.get("created_at"),
                    "score": round(final, 4), "factors": factors, "position": [row.get("x"), row.get("y"), row.get("z")]}
            if is_surface:
                if nid not in members_cache:
                    members_cache[nid] = rows(conn, "SELECT id, title FROM memory_nodes WHERE surface_id=? AND node_type='memory' ORDER BY created_at DESC LIMIT 8", (nid,))
                item["member_count"] = int(row.get("member_count") or 0)
                item["members"] = members_cache[nid]
                item["stale"] = bool(row.get("stale"))
                item["confidence"] = row.get("confidence")
            elif depth == "deep" or tier == "durable":
                item["user_text"] = row.get("user_text")
                item["assistant_text"] = row.get("assistant_text")
                item["content"] = (row.get("content") or "")[:4000]
            results.append(item)
        surfaces_summary = [{"id": i, "title": node_rows[i].get("title"), "score": round(sc, 4), "member_count": int(node_rows[i].get("member_count") or 0)} for sc, i in surfaces_ranked[:8]]
        return {"query": query, "results": results, "mode": mode, "depth_used": depth, "surfaces": surfaces_summary,
                "weights": {"text": weight_text, "geometry": weight_geo, "unified_self": w_self, "unified_surface": w_surface},
                "embedding_model": config.EMBED_MODEL, "embedded": len(vectors), "lexical_hits": len(lexical),
                "in_depth_loaded": depth == "deep"}
    finally:
        if own:
            conn.close()


def context_block(recalled: dict[str, Any]) -> str:
    depth = recalled.get("depth_used", "auto")
    surfaces = [r for r in recalled.get("results", []) if r.get("tier") == "surface"]
    deep = [r for r in recalled.get("results", []) if r.get("tier") == "in_depth"]
    durable = [r for r in recalled.get("results", []) if r.get("tier") == "durable"]
    lines: list[str] = []
    if surfaces:
        lines.append("SURFACE MEMORY (core meanings distilled from related exchanges; read these first):")
        for i, r in enumerate(surfaces):
            f = r["factors"]
            lines.append(f"- [surface | score {r['score']:.3f} | unified {f['unified_context']:.2f} | lexical {f['lexical_bm25']:.2f} | geometry {f['geometry_cosine']:.2f} | id {r['id'][:8]} | {r.get('member_count', 0)} exchanges]"
                         f"{' [STALE: not re-distilled yet]' if r.get('stale') else ''} {r['title']}: {r['summary']}")
            if r.get("members") and (depth == "deep" or i < 2):
                lines.append("    in-depth exchanges inside: " + "; ".join(f"{m['title'][:60]} (id {m['id'][:8]})" for m in r["members"][:8]))
    if durable:
        lines.append("DURABLE USER FACTS:")
        for r in durable:
            f = r["factors"]
            flag = " [SUPERSEDED/STALE — do not assert as current]" if f.get("superseded") else ""
            lines.append(f"- [{r['provenance']} | score {r['score']:.3f} | unified {f['unified_context']:.2f} | id {r['id'][:8]}]{flag} {r['title']}: {r['summary']}")
    if deep:
        lines.append("IN-DEPTH MEMORY (full question→response pairs loaded because deep thinking is on or the match was strong):")
        for r in deep:
            f = r["factors"]
            flag = " [SUPERSEDED/STALE — do not assert as current]" if f.get("superseded") else ""
            body = r.get("content") or r.get("summary") or ""
            lines.append(f"- [{r['provenance']} | score {r['score']:.3f} | unified {f['unified_context']:.2f} | surface {str(f.get('surface_id') or '')[:8]} | id {r['id'][:8]}]{flag} {r['title']}: {body[:900]}")
    if surfaces and depth != "deep":
        lines.append("In-depth exchanges were NOT loaded. If a surface memory is relevant but too compressed to answer precisely, call the memory_dive tool with its id before answering.")
    return "\n".join(lines) or "[none]"


def fallback_context(limit: int = 18) -> str:
    conn = connect()
    try:
        recent = rows(conn, "SELECT title, node_type, summary, tags_json FROM memory_nodes ORDER BY importance DESC, updated_at DESC LIMIT ?", (limit,))
    finally:
        conn.close()
    lines = []
    for r in recent:
        try:
            tags = ", ".join(json.loads(r["tags_json"] or "[]")[:4])
        except Exception:
            tags = ""
        lines.append(f"- [{r['node_type']}] {r['title']}: {r['summary'] or ''} {('tags: ' + tags) if tags else ''}")
    return "\n".join(lines) or "[none]"
