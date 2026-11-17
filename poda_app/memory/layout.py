"""Open3D-backed geometry and cosine→3D layout. Committed XYZ creates a separate spatial relevance signal."""
from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

from ..runtime import config
from ..runtime.db import now_iso, rows, one
from .store import ensure_embeddings, normalize, cosine

_GEOMETRY_CACHE: dict[str, Any] | None = None


def stable_unit_vector(seed_text: str) -> tuple[float, float, float]:
    digest = hashlib.sha256(seed_text.encode("utf-8", "ignore")).digest()
    a = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    b = int.from_bytes(digest[8:16], "big") / float(2**64 - 1)
    theta = 2.0 * math.pi * a
    z = (2.0 * b) - 1.0
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return r * math.cos(theta), z, r * math.sin(theta)


def classical_mds_3d(vectors: list[list[float]], scale: float = 120.0) -> list[list[float]]:
    import numpy as np
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [[0.0, 0.0, 0.0]]
    matrix = np.asarray(vectors, dtype=float)
    matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    similarity = np.clip(matrix @ matrix.T, -1.0, 1.0)
    distances = np.sqrt(np.maximum(0.0, 2.0 - 2.0 * similarity))
    d2 = distances ** 2
    centering = np.eye(n) - np.ones((n, n)) / n
    gram = -0.5 * centering @ d2 @ centering
    values, vecs = np.linalg.eigh(gram)
    order = np.argsort(values)[::-1][:3]
    coords = vecs[:, order] * np.sqrt(np.maximum(values[order], 0.0))
    if coords.shape[1] < 3:
        coords = np.pad(coords, ((0, 0), (0, 3 - coords.shape[1])))
    max_radius = float(np.max(np.linalg.norm(coords, axis=1))) if n else 1.0
    if max_radius > 1e-9:
        coords *= float(scale) / max_radius
    return coords[:, :3].tolist()


def _neighbor_links(conn, vectors: dict[str, list[float]], ids: list[str], neighbors: int, now: str) -> None:
    if len(ids) < 2:
        return
    import numpy as np
    matrix = np.asarray([vectors[i] for i in ids], dtype=float)
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -2.0)
    k = min(neighbors, len(ids) - 1)
    for i, source_id in enumerate(ids):
        top = np.argpartition(-sims[i], k)[:k]
        for j in top:
            strength = max(0.0, min(1.0, (float(sims[i, j]) + 1.0) / 2.0))
            conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'semantic_neighbor', ?, ?, ?)",
                         (str(uuid.uuid4()), source_id, ids[int(j)], strength, now, now))


def refresh_semantic_links(conn, vectors: dict[str, list[float]], neighbors: int = 8) -> None:
    """Materialize strongest cosine neighbours separately for surfaces and for in-depth nodes."""
    conn.execute("DELETE FROM memory_links WHERE relation='semantic_neighbor'")
    kinds = {r["id"]: r["node_type"] for r in rows(conn, "SELECT id, node_type FROM memory_nodes WHERE node_type IN ('memory','surface')")}
    now = now_iso()
    _neighbor_links(conn, vectors, [i for i in vectors if kinds.get(i) == "surface"], neighbors, now)
    _neighbor_links(conn, vectors, [i for i in vectors if kinds.get(i) == "memory"], neighbors, now)


_POSITION_SQL = (
    "UPDATE memory_nodes SET "
    "semantic_x=CASE WHEN COALESCE(position_source,'semantic')='committed' THEN semantic_x ELSE ? END, "
    "semantic_y=CASE WHEN COALESCE(position_source,'semantic')='committed' THEN semantic_y ELSE ? END, "
    "semantic_z=CASE WHEN COALESCE(position_source,'semantic')='committed' THEN semantic_z ELSE ? END, "
    "x=CASE WHEN COALESCE(position_source,'semantic') IN ('manual','committed') THEN x ELSE ? END, "
    "y=CASE WHEN COALESCE(position_source,'semantic') IN ('manual','committed') THEN y ELSE ? END, "
    "z=CASE WHEN COALESCE(position_source,'semantic') IN ('manual','committed') THEN z ELSE ? END, "
    "updated_at=? WHERE id=?"
)


def member_scale(count: int) -> float:
    return max(8.0, min(26.0, 6.0 + 2.2 * math.sqrt(max(1, count))))


def semantic_layout(conn, clear_manual_positions: bool = False) -> dict[str, Any]:
    """Three-level cosine layout: domain clouds → surface mini-clouds (and unassigned memories) → members around their surface."""
    if clear_manual_positions:
        conn.execute("UPDATE memory_nodes SET position_source='semantic' WHERE node_type IN ('memory','domain','surface')")
    vectors = ensure_embeddings(conn)
    memory_rows = rows(conn, "SELECT id, parent_id, surface_id FROM memory_nodes WHERE node_type='memory'")
    surface_rows = rows(conn, "SELECT id, parent_id, position_source FROM memory_nodes WHERE node_type='surface'")
    surface_ids = {s["id"] for s in surface_rows}
    by_parent_memories: dict[str, list[str]] = {}
    members_by_surface: dict[str, list[str]] = {}
    for row in memory_rows:
        by_parent_memories.setdefault(row["parent_id"] or "seed-personal-memory", []).append(row["id"])
        if row.get("surface_id") in surface_ids:
            members_by_surface.setdefault(row["surface_id"], []).append(row["id"])
    domain_rows = rows(conn, "SELECT id FROM memory_nodes WHERE node_type='domain'")
    dims = len(next(iter(vectors.values()))) if vectors else 8
    domain_vectors: list[list[float]] = []
    domain_ids: list[str] = []
    for domain in domain_rows:
        children = [vectors[n] for n in by_parent_memories.get(domain["id"], []) if n in vectors]
        if children:
            mean = [sum(v[i] for v in children) / len(children) for i in range(len(children[0]))]
            domain_vectors.append(normalize(mean))
        else:
            ux, uy, uz = stable_unit_vector("empty-domain:" + domain["id"])
            placeholder = [0.0] * dims
            placeholder[0], placeholder[1], placeholder[2] = ux, uy, uz
            domain_vectors.append(normalize(placeholder))
        domain_ids.append(domain["id"])
    centers: dict[str, tuple[float, float, float]] = {}
    for domain_id, coord in zip(domain_ids, classical_mds_3d(domain_vectors, scale=185.0)):
        x, y, z = map(float, coord)
        centers[domain_id] = (x, y, z)
        conn.execute(_POSITION_SQL, (x, y, z, x, y, z, now_iso(), domain_id))
    # Cloud level: surfaces plus memories that have no surface yet.
    cloud_items: dict[str, list[str]] = {}
    all_members = {nid for ids in members_by_surface.values() for nid in ids}
    for srow in surface_rows:
        cloud_items.setdefault(srow["parent_id"] or "seed-conversation-memory", []).append(srow["id"])
    for parent_id, child_ids in by_parent_memories.items():
        for nid in child_ids:
            if nid not in all_members:
                cloud_items.setdefault(parent_id, []).append(nid)
    for parent_id, item_ids in cloud_items.items():
        available = [n for n in item_ids if n in vectors]
        if not available:
            continue
        local = classical_mds_3d([vectors[n] for n in available], scale=min(92.0, 42.0 + math.sqrt(len(available)) * 13.0))
        cx, cy, cz = centers.get(parent_id, (0.0, 0.0, 0.0))
        for node_id, coord in zip(available, local):
            conn.execute(_POSITION_SQL, (cx + coord[0], cy + coord[1], cz + coord[2], cx + coord[0], cy + coord[1], cz + coord[2], now_iso(), node_id))
    # Member level: around the surface's *actual* position (committed surfaces keep their place; members follow).
    for surface_id, member_ids in members_by_surface.items():
        srow = one(conn, "SELECT x, y, z FROM memory_nodes WHERE id=?", (surface_id,)) or {}
        sx, sy, sz = float(srow.get("x") or 0.0), float(srow.get("y") or 0.0), float(srow.get("z") or 0.0)
        available = [n for n in member_ids if n in vectors]
        if not available:
            continue
        local = classical_mds_3d([vectors[n] for n in available], scale=member_scale(len(available)))
        if len(available) == 1:
            ux, uy, uz = stable_unit_vector("member:" + available[0])
            local = [[ux * 6.0, uy * 6.0, uz * 6.0]]
        for node_id, coord in zip(available, local):
            conn.execute(_POSITION_SQL, (sx + coord[0], sy + coord[1], sz + coord[2], sx + coord[0], sy + coord[1], sz + coord[2], now_iso(), node_id))
    refresh_semantic_links(conn, vectors)
    return {"embedding_model": config.EMBED_MODEL, "embedded_memories": sum(1 for i in vectors if i not in surface_ids), "surfaces": len(surface_ids), "clouds": len(domain_ids)}


def display_positions(conn) -> dict[str, Any]:
    """Fast scene read: preserve saved geometry; initialize only unpositioned points.
    Semantic reflow is an explicit operation, not a side effect of opening Memory.
    """
    missing = rows(conn, "SELECT id,parent_id,node_type FROM memory_nodes WHERE node_type!='transient' AND (x IS NULL OR y IS NULL OR z IS NULL)")
    centers = {r['id']:(r['x'] or 0,r['y'] or 0,r['z'] or 0) for r in rows(conn,'SELECT id,x,y,z FROM memory_nodes')}
    for row in sorted(missing,key=lambda r: {'root':0,'domain':1,'surface':2}.get(r['node_type'],3)):
        center = centers.get(row['parent_id'],(0,0,0)); unit = stable_unit_vector(row['id'])
        radius = 165 if row['node_type']=='domain' else 35
        xyz = (0,0,0) if row['node_type']=='root' else tuple(center[i]+unit[i]*radius for i in range(3))
        conn.execute('UPDATE memory_nodes SET x=?,y=?,z=? WHERE id=?',(*xyz,row['id']));centers[row['id']]=xyz
    return {'mode':'saved_geometry','initialized':len(missing)}


def ensure_positions(conn) -> dict[str, Any]:
    """Prefer the semantic cosine layout; fall back to deterministic geometry if embeddings are unavailable."""
    try:
        result = semantic_layout(conn)
        result["mode"] = "cosine_mds_3d"
        return result
    except Exception as exc:
        all_rows = rows(conn, "SELECT id, parent_id, node_type, x, y, z FROM memory_nodes WHERE node_type != 'transient'")
        positioned = {r["id"]: r for r in all_rows}
        now = now_iso()
        counts: dict[str, int] = {}
        for row in all_rows:
            if row["x"] is not None and row["y"] is not None and row["z"] is not None:
                continue
            if row["node_type"] == "root":
                conn.execute("UPDATE memory_nodes SET x=0,y=0,z=0,updated_at=? WHERE id=?", (now, row["id"]))
            elif row["node_type"] == "domain":
                ux, uy, uz = stable_unit_vector("domain:" + row["id"])
                conn.execute("UPDATE memory_nodes SET x=?,y=?,z=?,updated_at=? WHERE id=?", (ux * 165, uy * 165, uz * 165, now, row["id"]))
            else:
                parent_id = row["parent_id"] or "seed-personal-memory"
                parent = positioned.get(parent_id)
                px, py, pz = (float(parent["x"] or 0), float(parent["y"] or 0), float(parent["z"] or 0)) if parent else (0.0, 0.0, 0.0)
                idx = counts.get(parent_id, 0)
                counts[parent_id] = idx + 1
                ux, uy, uz = stable_unit_vector(f"memory:{row['id']}:{idx}")
                conn.execute("UPDATE memory_nodes SET x=?,y=?,z=?,updated_at=? WHERE id=?", (px + ux * 55, py + uy * 55, pz + uz * 55, now, row["id"]))
        return {"mode": "deterministic_fallback", "embedding_model": config.EMBED_MODEL, "error": str(exc), "embedded_memories": 0}


def semantic_position(row: dict[str, Any]) -> tuple[float, float, float]:
    sx, sy, sz = row.get("semantic_x"), row.get("semantic_y"), row.get("semantic_z")
    if sx is None or sy is None or sz is None:
        sx, sy, sz = row.get("x") or 0.0, row.get("y") or 0.0, row.get("z") or 0.0
    return float(sx or 0.0), float(sy or 0.0), float(sz or 0.0)


def geometry_scale(node_rows: dict[str, dict[str, Any]]) -> float:
    """World-space scale converting committed 3D chord distance into a cosine-like score."""
    positions = [semantic_position(r) for r in node_rows.values()]
    if len(positions) < 2:
        return 90.0
    import numpy as np
    pts = np.asarray(positions, dtype=float)
    if len(pts) > 1200:  # sample for scale estimation at large n
        idx = np.random.default_rng(7).choice(len(pts), 1200, replace=False)
        pts = pts[idx]
    diff = pts[:, None, :] - pts[None, :, :]
    d = np.sqrt((diff ** 2).sum(-1))[np.triu_indices(len(pts), 1)]
    d = d[d > 1e-6]
    if d.size == 0:
        return 90.0
    return max(18.0, float(np.percentile(d, 95)) / 2.0)


def geometry_cosine(a: dict[str, Any], b: dict[str, Any], scale: float) -> float:
    ax, ay, az = semantic_position(a)
    bx, by, bz = semantic_position(b)
    d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
    chord = max(0.0, min(2.0, d / max(1.0, float(scale))))
    return max(-1.0, min(1.0, 1.0 - 0.5 * chord * chord))


def refresh_committed_spatial_links(conn, neighbors: int = 16) -> None:
    conn.execute("DELETE FROM memory_links WHERE relation IN ('committed_spatial','geometry_cosine')")
    for node_type in ("memory", "surface"):
        _spatial_links_for(conn, {r["id"]: r for r in rows(conn, "SELECT * FROM memory_nodes WHERE node_type=?", (node_type,))}, neighbors)


def _spatial_links_for(conn, node_rows: dict[str, dict[str, Any]], neighbors: int) -> None:
    if len(node_rows) < 2:
        return
    import numpy as np
    ids = list(node_rows)
    pts = np.asarray([semantic_position(node_rows[i]) for i in ids], dtype=float)
    scale = max(1.0, geometry_scale(node_rows))
    diff = pts[:, None, :] - pts[None, :, :]
    chord = np.clip(np.sqrt((diff ** 2).sum(-1)) / scale, 0.0, 2.0)
    cosine_like = np.clip(1.0 - 0.5 * chord * chord, -1.0, 1.0)
    np.fill_diagonal(cosine_like, -2.0)
    now = now_iso()
    k = min(neighbors, len(ids) - 1)
    for i, source_id in enumerate(ids):
        for j in np.argpartition(-cosine_like[i], k)[:k]:
            strength = (float(cosine_like[i, j]) + 1.0) / 2.0
            conn.execute("INSERT OR IGNORE INTO memory_links (id, source_id, target_id, relation, strength, created_at, updated_at) VALUES (?, ?, ?, 'geometry_cosine', ?, ?, ?)",
                         (str(uuid.uuid4()), source_id, ids[int(j)], strength, now, now))


def open3d_geometry_template() -> dict[str, Any]:
    """Portable unit sphere for WebGL; avoid loading Open3D just to read Memory."""
    global _GEOMETRY_CACHE
    if _GEOMETRY_CACHE is not None: return _GEOMETRY_CACHE
    rings, segments = 24,48
    vertices, triangles = [],[]
    for ring in range(rings+1):
        theta = math.pi*ring/rings
        for segment in range(segments):
            phi = 2*math.pi*segment/segments
            vertices.append([round(math.sin(theta)*math.cos(phi),6),round(math.cos(theta),6),round(math.sin(theta)*math.sin(phi),6)])
    for ring in range(rings):
        for segment in range(segments):
            a=ring*segments+segment;b=ring*segments+(segment+1)%segments;c=a+segments;d=b+segments
            if ring: triangles.append([a,c,b])
            if ring<rings-1: triangles.append([b,c,d])
    _GEOMETRY_CACHE = {'cloud_volume':{'vertices':vertices,'triangles':triangles,'normals':vertices,
                       'vertex_count':len(vertices),'triangle_count':len(triangles),'resolution':rings},'open3d_version':'portable-mesh'}
    return _GEOMETRY_CACHE


def scene_bounds(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Axis-aligned bounds without importing a native 3D engine on a page read."""
    if not nodes:
        return {"center": [0.0, 0.0, 0.0], "extent": [1.0, 1.0, 1.0], "diagonal": 1.0}
    axes = [[float(n.get(key) or 0.0) for n in nodes] for key in ('x', 'y', 'z')]
    lo, hi = [min(a) for a in axes], [max(a) for a in axes]
    extent = [hi[i] - lo[i] for i in range(3)]
    return {"center": [round((lo[i] + hi[i]) / 2, 5) for i in range(3)],
            "extent": [round(v, 5) for v in extent], "diagonal": max(1.0, math.sqrt(sum(v*v for v in extent)))}
