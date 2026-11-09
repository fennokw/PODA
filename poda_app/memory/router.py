"""Memory API: graph, Open3D scene, transactional commit with version history, recall explanations, search."""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime import config
from ..runtime.db import connect, now_iso, rows, one, get_setting
from . import store, layout, retrieval, surface
from .embedding_jobs import enqueue as enqueue_embedding_jobs, refresh_pending as refresh_pending_embeddings, pending_count

router = APIRouter()


class MemoryNodeCreate(BaseModel):
    title: str
    parent_id: str | None = None
    node_type: str = "memory"
    summary: str = ""
    content: str = ""
    reference_triggers: str = ""
    tags: list[str] = []
    x: float | None = None
    y: float | None = None
    z: float | None = None
    pinned: bool = False
    importance: float = 0.5


class MemoryNodeUpdate(BaseModel):
    title: str | None = None
    parent_id: str | None = None
    node_type: str | None = None
    summary: str | None = None
    content: str | None = None
    reference_triggers: str | None = None
    tags: list[str] | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    pinned: bool | None = None
    importance: float | None = None


class MemoryLinkCreate(BaseModel):
    source_id: str
    target_id: str
    relation: str = "related_to"
    strength: float = 0.5


class SemanticReflowRequest(BaseModel):
    clear_manual_positions: bool = False


class MemoryDraftNode(BaseModel):
    id: str
    parent_id: str | None = None
    surface_id: str | None = None
    title: str
    node_type: str = "memory"
    summary: str = ""
    content: str = ""
    reference_triggers: str = ""
    tags: list[str] = []
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    pinned: bool = True
    importance: float = 0.5


class MemoryCommitRequest(BaseModel):
    nodes: list[MemoryDraftNode]
    deleted_ids: list[str] = []
    note: str | None = None
    dissolve: bool = False  # required to delete a surface that still has members


class RecallRequest(BaseModel):
    query: str
    limit: int = 18
    depth: str = "auto"  # auto | surface | deep


class SurfaceRebuildRequest(BaseModel):
    mode: str = "incremental"
    confirm: bool = False


class SurfaceMembersRequest(BaseModel):
    add: list[str] = []
    remove: list[str] = []


@router.get("/memory")
def get_memory() -> dict[str, Any]:
    conn = connect()
    try:
        return store.list_graph(conn)
    finally:
        conn.close()


@router.get("/memory/open3d/scene")
def open3d_scene() -> dict[str, Any]:
    conn = connect()
    try:
        semantic = layout.ensure_positions(conn)
        conn.commit()
        graph = store.list_graph(conn)
    finally:
        conn.close()
    try:
        geometry = layout.open3d_geometry_template()
        bounds = layout.scene_bounds(graph["nodes"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Open3D memory renderer is unavailable: {exc}")
    tiers = {"surfaces": 0, "in_depth": 0, "durable": 0}
    for n in graph["nodes"]:
        if n["node_type"] == "surface":
            n["radius"] = surface.radius_for(n.get("member_count") or 0)
            tiers["surfaces"] += 1
        elif n["node_type"] == "memory":
            tiers["in_depth" if n.get("tier") == "in_depth" else "durable"] += 1
    show_default = get_setting("surface_show_default", "1")
    return {
        "renderer": "Open3D translucent cloud volumes + surface mini-clouds + in-depth point web",
        "open3d": {"available": True, "version": geometry["open3d_version"]},
        "cloud_volume": geometry["cloud_volume"], "bounds": bounds,
        "nodes": graph["nodes"], "semantic_links": graph["links"], "semantic_layout": semantic,
        "tiers": tiers, "settings": {"surface_show_default": show_default not in {"0", "false", "off"}, "surface_join_threshold": surface.join_threshold_value()},
        "connection_rule": "surfaces_form_a_web; in_depth_members_reveal_inside_their_surface",
        "semantic_rule": "committed_3d_distance_updates_geometry_cosine_relationships; surface_cosine_propagates_to_members",
        "build_id": config.BUILD_ID,
    }


@router.post("/memory/recall")
def memory_recall(req: RecallRequest) -> dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    depth = req.depth if req.depth in {"auto", "surface", "deep"} else "auto"
    return retrieval.recall(req.query, limit=max(1, min(req.limit, 60)), depth=depth)


@router.get("/memory/search")
def memory_search(q: str, limit: int = 50, kind: str | None = None, speaker: str | None = None, parent_id: str | None = None,
                  since: str | None = None, until: str | None = None, tier: str | None = None, surface_id: str | None = None) -> dict[str, Any]:
    """Full-database lexical search across surfaces and in-depth nodes (works for nodes hidden by level of detail)."""
    conn = connect()
    try:
        match = retrieval._fts_query(q)
        where = ["n.node_type IN ('memory','surface')"]
        params: list[Any] = []
        if tier == "surface":
            where.append("n.node_type='surface'")
        elif tier == "in_depth":
            where.append("n.node_type='memory' AND COALESCE(n.memory_kind,'durable') != 'durable'")
        elif tier == "durable":
            where.append("n.node_type='memory' AND COALESCE(n.memory_kind,'durable') = 'durable'")
        if surface_id:
            where.append("n.surface_id=?"); params.append(surface_id)
        if kind:
            where.append("n.memory_kind=?"); params.append(kind)
        if speaker:
            where.append("n.speaker=?"); params.append(speaker)
        if parent_id:
            where.append("n.parent_id=?"); params.append(parent_id)
        if since:
            where.append("n.created_at>=?"); params.append(since)
        if until:
            where.append("n.created_at<=?"); params.append(until)
        if match:
            sql = f"""SELECT n.*, bm25(memory_fts, 3.0, 1.5, 1.0, 2.5, 1.0) AS rank FROM memory_fts f JOIN memory_nodes n ON n.id=f.node_id
                      WHERE memory_fts MATCH ? AND {' AND '.join(where)} ORDER BY rank LIMIT ?"""
            found = rows(conn, sql, [match, *params, max(1, min(limit, 500))])
        else:
            found = rows(conn, f"SELECT n.* FROM memory_nodes n WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?", [*params, max(1, min(limit, 500))])
        return {"query": q, "count": len(found), "results": [store.node_to_api(r) for r in found]}
    finally:
        conn.close()


@router.get("/memory/transactions")
def memory_transactions(limit: int = 50) -> dict[str, Any]:
    conn = connect()
    try:
        return {"transactions": rows(conn, "SELECT * FROM memory_transactions ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),))}
    finally:
        conn.close()


@router.get("/memory/versions/{node_id}")
def memory_versions(node_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        versions = rows(conn, "SELECT * FROM memory_versions WHERE node_id=? ORDER BY created_at DESC LIMIT 100", (node_id,))
        for v in versions:
            for k in ("before_json", "after_json"):
                try:
                    v[k.replace("_json", "")] = json.loads(v.pop(k) or "null")
                except Exception:
                    v[k.replace("_json", "")] = None
        return {"node_id": node_id, "versions": versions}
    finally:
        conn.close()


@router.post("/memory/transaction/commit")
def commit_transaction(req: MemoryCommitRequest) -> dict[str, Any]:
    """Atomically commit the visual draft. Records a transaction plus per-node before/after versions."""
    if len(req.nodes) > 5000:
        raise HTTPException(status_code=400, detail="Memory transaction is too large")
    conn = connect()
    tx_id = str(uuid.uuid4())
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {r["id"]: r for r in rows(conn, "SELECT * FROM memory_nodes WHERE node_type != 'transient'")}
        allowed_delete = {nid for nid in req.deleted_ids if nid != store.ROOT_ID}
        now = now_iso()
        text_changes = position_changes = 0

        def record(node_id: str, change: str, before: dict | None, after: dict | None) -> None:
            conn.execute("INSERT INTO memory_versions (id, transaction_id, node_id, change, before_json, after_json, created_at) VALUES (?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), tx_id, node_id, change, json.dumps(before, default=str) if before else None, json.dumps(after, default=str) if after else None, now))

        touched_surfaces: set[str] = set()
        for node_id in allowed_delete:
            before = existing.get(node_id) or one(conn, "SELECT * FROM memory_nodes WHERE id=?", (node_id,))
            if before and before.get("node_type") == "surface":
                members = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE surface_id=?", (node_id,)).fetchone()[0]
                if members and not req.dissolve:
                    raise HTTPException(status_code=400, detail={"code": "surface_has_members", "message": f"Surface '{before.get('title')}' still contains {members} in-depth memories. Re-send with dissolve=true to release them (they will be re-assigned by the next rebuild)."})
                conn.execute("UPDATE memory_nodes SET surface_id=NULL, updated_at=? WHERE surface_id=?", (now, node_id))
            if before and before.get("surface_id"):
                touched_surfaces.add(before["surface_id"])
            conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (node_id, node_id))
            conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node_id,))
            conn.execute("DELETE FROM memory_nodes WHERE id=?", (node_id,))
            if before and before.get("source_message_id"):
                conn.execute("DELETE FROM messages WHERE id=?", (before["source_message_id"],))
            if before and before.get("response_message_id"):
                conn.execute("DELETE FROM messages WHERE id=?", (before["response_message_id"],))
            record(node_id, "delete", before, None)

        order_rank = {"domain": 0, "surface": 1, "memory": 2}
        ordered = sorted(req.nodes, key=lambda n: order_rank.get(n.node_type, 3))
        valid_domains = {r["id"] for r in rows(conn, "SELECT id FROM memory_nodes WHERE node_type='domain'")}
        valid_domains.update(n.id for n in req.nodes if n.node_type == "domain")
        valid_surfaces = {r["id"] for r in rows(conn, "SELECT id FROM memory_nodes WHERE node_type='surface'")} - allowed_delete
        valid_surfaces.update(n.id for n in req.nodes if n.node_type == "surface" and n.id not in allowed_delete)
        changed_text: set[str] = set()
        for node in ordered:
            if node.id == store.ROOT_ID or node.id in allowed_delete:
                continue
            if node.node_type not in {"domain", "memory", "surface"}:
                raise HTTPException(status_code=400, detail=f"Unsupported draft node type: {node.node_type}")
            parent_id = store.ROOT_ID if node.node_type == "domain" else (node.parent_id or store.PERSONAL_CLOUD)
            if node.node_type in {"memory", "surface"} and parent_id not in valid_domains:
                raise HTTPException(status_code=400, detail=f"Memory parent cloud does not exist: {parent_id}")
            new_surface = node.surface_id if (node.node_type == "memory" and node.surface_id in valid_surfaces) else None
            current = existing.get(node.id)
            tags_json = json.dumps(node.tags[:20])
            after = {"parent_id": parent_id, "title": node.title[:180], "summary": node.summary[:2000], "content": node.content[:10000], "reference_triggers": node.reference_triggers[:3000],
                     "tags_json": tags_json, "x": float(node.x), "y": float(node.y), "z": float(node.z), "pinned": 1 if node.pinned else 0, "importance": float(node.importance)}
            if node.node_type == "memory":
                after["surface_id"] = new_surface
            if current:
                moved = any(abs(float(current.get(k) or 0.0) - after[k]) > 1e-6 for k in ("x", "y", "z"))
                text_changed = any((current.get(k) or "") != after[k] for k in ("title", "summary", "content", "reference_triggers", "tags_json"))
                membership_changed = (current.get("parent_id") or "") != parent_id
                surface_changed = node.node_type == "memory" and (current.get("surface_id") or None) != new_surface and node.surface_id is not None
                if not (moved or text_changed or membership_changed or surface_changed or (current.get("pinned") or 0) != after["pinned"] or abs(float(current.get("importance") or 0) - after["importance"]) > 1e-9):
                    continue
                conn.execute("""UPDATE memory_nodes SET parent_id=?, title=?, node_type=?, summary=?, content=?, reference_triggers=?, tags_json=?, x=?, y=?, z=?,
                                semantic_x=?, semantic_y=?, semantic_z=?, pinned=?, importance=?, position_source='committed', updated_at=? WHERE id=?""",
                             (parent_id, after["title"], node.node_type, after["summary"], after["content"], after["reference_triggers"], tags_json,
                              after["x"], after["y"], after["z"], after["x"], after["y"], after["z"], after["pinned"], after["importance"], now, node.id))
                if surface_changed:
                    surface.set_membership(conn, node.id, new_surface)
                    touched_surfaces.update(x for x in (current.get("surface_id"), new_surface) if x)
                if text_changed:
                    changed_text.add(node.id)
                    text_changes += 1
                    if node.node_type == "surface":
                        conn.execute("UPDATE memory_nodes SET user_edited=1, stale=0 WHERE id=?", (node.id,))
                    elif current.get("surface_id"):
                        touched_surfaces.add(current["surface_id"])
                if moved:
                    position_changes += 1
                record(node.id, "update", {k: current.get(k) for k in after}, after)
            else:
                kind = "surface" if node.node_type == "surface" else "durable"
                conn.execute("""INSERT INTO memory_nodes (id, parent_id, title, node_type, summary, content, reference_triggers, tags_json, x, y, z, semantic_x, semantic_y, semantic_z,
                                position_source, pinned, importance, memory_kind, speaker, confidence, user_edited, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'committed',?,?,?,?,0.9,?,?,?)""",
                             (node.id, parent_id, after["title"], node.node_type, after["summary"], after["content"], after["reference_triggers"], tags_json,
                              after["x"], after["y"], after["z"], after["x"], after["y"], after["z"], after["pinned"], after["importance"], kind,
                              "surface" if kind == "surface" else None, 1 if kind == "surface" else 0, now, now))
                if node.node_type == "memory" and new_surface:
                    surface.set_membership(conn, node.id, new_surface)
                    touched_surfaces.add(new_surface)
                changed_text.add(node.id)
                text_changes += 1
                record(node.id, "create", None, after)

        committed_ids = {n.id for n in req.nodes if n.id not in allowed_delete}
        if committed_ids:
            placeholders = ",".join("?" for _ in committed_ids)
            conn.execute(f"DELETE FROM memory_links WHERE relation='contains' AND target_id IN ({placeholders})", tuple(committed_ids))
        for node in req.nodes:
            if node.id == store.ROOT_ID or node.id in allowed_delete:
                continue
            parent_id = store.ROOT_ID if node.node_type == "domain" else (node.parent_id or store.PERSONAL_CLOUD)
            conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.82, ?, ?)",
                         (str(uuid.uuid4()), parent_id, node.id, now, now))
        for node_id in changed_text:
            conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node_id,))
        for sid in touched_surfaces:
            if one(conn, "SELECT id FROM memory_nodes WHERE id=?", (sid,)):
                surface.refresh_counts(conn, sid)
        # Position-only changes and deletes need no Ollama. Text changes are queued transactionally
        # and embedded AFTER commit; losing Ollama must not roll back a valid memory edit.
        enqueue_embedding_jobs(conn, changed_text)
        layout.refresh_committed_spatial_links(conn)
        conn.execute("INSERT INTO memory_transactions (id, created_at, summary, node_count, deleted_count, text_changes, position_changes) VALUES (?,?,?,?,?,?,?)",
                     (tx_id, now, (req.note or "memory viewer commit")[:400], len(req.nodes), len(allowed_delete), text_changes, position_changes))
        conn.commit()
        # Do not hold a write transaction while waiting on the Ollama embedding server.
        # A follow-up retry endpoint and normal retrieval can reconcile outstanding jobs.
        embed_result = refresh_pending_embeddings(limit=24) if changed_text else {"refreshed": 0, "pending": pending_count()}
        return {"status": "committed", "transaction_id": tx_id, "nodes": len(req.nodes), "deleted": len(allowed_delete),
                "text_embeddings_refreshed": embed_result["refreshed"], "embeddings_pending": embed_result["pending"], "position_changes": position_changes, "surfaces_touched": len(touched_surfaces),
                "retrieval": "72% direct text cosine + 28% committed geometry cosine propagation (plus lexical/trigger reranking)"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Memory transaction failed and was rolled back: {exc}")
    finally:
        conn.close()


@router.post("/memory/nodes")
def create_node(req: MemoryNodeCreate) -> dict[str, Any]:
    conn = connect()
    try:
        nid = str(uuid.uuid4())
        conn.execute("""INSERT INTO memory_nodes (id, parent_id, title, node_type, summary, content, reference_triggers, tags_json, x, y, z, pinned, importance, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (nid, req.parent_id, req.title, req.node_type, req.summary, req.content, req.reference_triggers, json.dumps(req.tags), req.x, req.y, req.z, 1 if req.pinned else 0, req.importance, now_iso(), now_iso()))
        if req.parent_id:
            conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.72, ?, ?)",
                         (str(uuid.uuid4()), req.parent_id, nid, now_iso(), now_iso()))
        if req.node_type == "memory":
            store.related_links(conn, nid, req.parent_id or store.PERSONAL_CLOUD, req.tags)
        conn.commit()
        return {"status": "created", "id": nid}
    finally:
        conn.close()


@router.put("/memory/nodes/{node_id}")
def update_node(node_id: str, req: MemoryNodeUpdate) -> dict[str, Any]:
    conn = connect()
    try:
        current = one(conn, "SELECT * FROM memory_nodes WHERE id=?", (node_id,))
        if not current:
            raise HTTPException(status_code=404, detail="Node not found")
        updates, values = ["updated_at=?"], [now_iso()]
        for key in ["title", "parent_id", "node_type", "summary", "content", "reference_triggers", "x", "y", "z", "importance"]:
            val = getattr(req, key)
            if val is not None:
                updates.append(f"{key}=?"); values.append(val)
        if req.x is not None or req.y is not None or req.z is not None:
            updates.append("position_source=?"); values.append("manual")
        if req.tags is not None:
            updates.append("tags_json=?"); values.append(json.dumps(req.tags))
        if req.pinned is not None:
            updates.append("pinned=?"); values.append(1 if req.pinned else 0)
        values.append(node_id)
        conn.execute(f"UPDATE memory_nodes SET {', '.join(updates)} WHERE id=?", tuple(values))
        if req.parent_id is not None and req.parent_id != current["parent_id"]:
            conn.execute("DELETE FROM memory_links WHERE target_id=? AND relation='contains'", (node_id,))
            conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'contains', 0.72, ?, ?)",
                         (str(uuid.uuid4()), req.parent_id, node_id, now_iso(), now_iso()))
        if any(getattr(req, k) is not None for k in ["title", "summary", "content", "tags", "reference_triggers"]):
            conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node_id,))
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/memory/nodes/{node_id}")
def delete_node(node_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        before = one(conn, "SELECT * FROM memory_nodes WHERE id=?", (node_id,))
        if before and before.get("node_type") == "surface":
            conn.execute("UPDATE memory_nodes SET surface_id=NULL WHERE surface_id=?", (node_id,))
        conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (node_id, node_id))
        conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node_id,))
        conn.execute("DELETE FROM memory_nodes WHERE id=?", (node_id,))
        if before and before.get("source_message_id"):
            conn.execute("DELETE FROM messages WHERE id=?", (before["source_message_id"],))
        if before and before.get("response_message_id"):
            conn.execute("DELETE FROM messages WHERE id=?", (before["response_message_id"],))
        if before and before.get("surface_id") and one(conn, "SELECT id FROM memory_nodes WHERE id=?", (before["surface_id"],)):
            surface.refresh_counts(conn, before["surface_id"])
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@router.post("/memory/links")
def create_link(req: MemoryLinkCreate) -> dict[str, Any]:
    conn = connect()
    try:
        eid = str(uuid.uuid4())
        conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (eid, req.source_id, req.target_id, req.relation, req.strength, now_iso(), now_iso()))
        conn.commit()
        return {"status": "created", "id": eid}
    finally:
        conn.close()


@router.post("/memory/semantic/reflow")
def reflow(req: SemanticReflowRequest) -> dict[str, Any]:
    conn = connect()
    try:
        result = layout.semantic_layout(conn, clear_manual_positions=req.clear_manual_positions)
        conn.commit()
        return {"status": "reflowed", **result}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail=f"Semantic memory reflow failed: {exc}")
    finally:
        conn.close()


# ----------------------------------------------------------------------------------------
# Surface memory endpoints
# ----------------------------------------------------------------------------------------

def _surface_api(row: dict[str, Any]) -> dict[str, Any]:
    out = store.node_to_api(row)
    out["radius"] = surface.radius_for(out.get("member_count") or 0)
    return out


@router.get("/memory/surfaces/status")
def surfaces_status() -> dict[str, Any]:
    return surface.status()


@router.get("/memory/surfaces")
def list_surfaces(stale_only: bool = False) -> dict[str, Any]:
    conn = connect()
    try:
        where = "WHERE node_type='surface'" + (" AND stale=1" if stale_only else "")
        return {"surfaces": [_surface_api(r) for r in rows(conn, f"SELECT * FROM memory_nodes {where} ORDER BY member_count DESC, updated_at DESC")]}
    finally:
        conn.close()


@router.get("/memory/surfaces/{surface_id}")
def get_surface(surface_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        row = one(conn, "SELECT * FROM memory_nodes WHERE id=? AND node_type='surface'", (surface_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Surface not found")
        members = [store.node_to_api(m) for m in rows(conn, "SELECT * FROM memory_nodes WHERE surface_id=? AND node_type='memory' ORDER BY created_at ASC", (surface_id,))]
        return {**_surface_api(row), "members": members}
    finally:
        conn.close()


@router.post("/memory/surfaces/rebuild")
def rebuild_surfaces(req: SurfaceRebuildRequest) -> dict[str, Any]:
    mode = req.mode if req.mode in {"incremental", "full"} else "incremental"
    if mode == "full" and not req.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirm_required", "message": "A full rebuild re-clusters every in-depth memory into new surfaces (user-edited surfaces are kept). Re-send with confirm=true."})
    conn = connect()
    try:
        result = surface.rebuild(conn, mode)
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail=f"Surface rebuild failed: {exc}")
    finally:
        conn.close()


@router.post("/memory/surfaces/{surface_id}/distill")
def distill_surface(surface_id: str, force: bool = False) -> dict[str, Any]:
    conn = connect()
    try:
        result = surface.distill_surface(conn, surface_id, force=force)
        if not result:
            raise HTTPException(status_code=404, detail="Surface not found")
        conn.commit()
        return _surface_api(result) | {"distill_method": result.get("distill_method")}
    finally:
        conn.close()


@router.post("/memory/surfaces/{surface_id}/members")
def surface_members(surface_id: str, req: SurfaceMembersRequest) -> dict[str, Any]:
    conn = connect()
    try:
        if not one(conn, "SELECT id FROM memory_nodes WHERE id=? AND node_type='surface'", (surface_id,)):
            raise HTTPException(status_code=404, detail="Surface not found")
        for nid in req.add:
            if one(conn, "SELECT id FROM memory_nodes WHERE id=? AND node_type='memory'", (nid,)):
                surface.set_membership(conn, nid, surface_id)
        for nid in req.remove:
            node = one(conn, "SELECT id, surface_id FROM memory_nodes WHERE id=?", (nid,))
            if node and node.get("surface_id") == surface_id:
                surface.set_membership(conn, nid, None)
        conn.commit()
        return get_surface(surface_id)
    finally:
        conn.close()


@router.get("/memory/embeddings/status")
def embedding_status() -> dict[str, Any]:
    return {"pending": pending_count(), "source": "local embedding queue"}


@router.post("/memory/embeddings/retry")
def retry_embeddings() -> dict[str, Any]:
    return refresh_pending_embeddings(limit=48)
