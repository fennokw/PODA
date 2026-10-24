"""Local-only Hindsight engine adapter synchronized to PODA's canonical graph.

PODA memory_nodes is the source of truth. Hindsight's per-node documents are
a MATERIALIZED INDEX, not a second PODA chat history or independent memory tree.
Deletion and edits reconcile by stable document_id (poda-node-<uuid>).
Optional: a self-hosted Hindsight server configured to use local Ollama.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime.db import connect, get_setting, set_setting, rows, one, now_iso

router=APIRouter(prefix="/memory/hindsight",tags=["memory"])
BASE="http://127.0.0.1:8888"
BANK="poda-local-graph"
MAX_SYNC_PER_CALL=35


def enabled()->bool:
    return get_setting("hindsight_local_enabled","false")=="true"


def _path(suffix:str)->str:
    return f"/v1/default/banks/{BANK}"+suffix


def _request(method:str,path:str,payload:dict|None=None)->dict:
    # Fixed loopback host, no user-controllable address and no environment proxy.
    # Local Ollama fact extraction can exceed ordinary HTTP read budgets;
    # recall is bounded tightly so chat remains responsive on outage.
    operation_timeout = 120 if method=="POST" and path.endswith("/memories") else 8
    with httpx.Client(base_url=BASE,timeout=operation_timeout,trust_env=False,follow_redirects=False) as client:
        response=client.request(method,path,json=payload)
        response.raise_for_status()
        return response.json() if response.content else {}


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS hindsight_node_index (
      poda_node_id TEXT PRIMARY KEY, content_sha TEXT NOT NULL,
      status TEXT NOT NULL, last_synced TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS poda_memory_feedback(
      poda_node_id TEXT PRIMARY KEY, assessment TEXT NOT NULL,
      reason TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def _document_id(node_id:str)->str:
    return "poda-node-"+node_id


def _canonical_nodes(db):
    # No invisible automation transcripts; only visible committed memory nodes.
    return rows(db,"""SELECT n.id,n.title,n.summary,n.content,n.parent_id,n.surface_id,n.tags_json,n.importance
              FROM memory_nodes n LEFT JOIN poda_memory_feedback f ON f.poda_node_id=n.id
              WHERE n.node_type='memory' AND n.superseded_by IS NULL AND n.valid_until IS NULL
                AND COALESCE(f.assessment,'') NOT IN ('incorrect','outdated')
              ORDER BY n.updated_at ASC""")


def sync_once(limit:int=MAX_SYNC_PER_CALL)->dict[str,Any]:
    if not enabled():
        return {"enabled":False,"synchronized":0,"deleted":0}
    limit=max(1,min(int(limit),MAX_SYNC_PER_CALL))
    db=connect()
    synced=deleted=0;errors=[]
    try:
        ensure_tables(db)
        all_nodes=_canonical_nodes(db)
        current={n["id"]:n for n in all_nodes}
        existing={n["poda_node_id"]:n for n in rows(db,"SELECT * FROM hindsight_node_index")}
        # Propagate deletions BEFORE any new retains. Treat absence from PODA
        # as an explicit invalidation; never import inferred Hindsight facts
        # as independent visible PODA nodes.
        for missing in sorted(set(existing)-set(current))[:limit]:
            try:
                _request("DELETE",_path("/documents/"+_document_id(missing)))
                db.execute("DELETE FROM hindsight_node_index WHERE poda_node_id=?",(missing,))
                db.commit();deleted+=1
            except Exception as exc:errors.append(f"delete {missing}: {type(exc).__name__}")
        for n in all_nodes:
            if synced>=limit:break
            text="\n".join(x for x in (n["title"],n["summary"],n["content"]) if x)[:8000]
            checksum=hashlib.sha256((text+"|"+str(n["parent_id"])+"|"+str(n["surface_id"])).encode()).hexdigest()
            old=existing.get(n["id"])
            if old and old["content_sha"]==checksum and old["status"]=="synced":continue
            payload={"async":False,"items":[{"content":text,
                "document_id":_document_id(n["id"]), "context":"PODA canonical memory node",
                "metadata":{"poda_node_id":n["id"],"parent_id":str(n["parent_id"] or ""),
                            "surface_id":str(n["surface_id"] or ""),
                            "origin":"PODA","priority":str(n["importance"] or 0.5)}}]}
            try:
                _request("POST",_path("/memories"),payload)
                db.execute("""INSERT INTO hindsight_node_index(poda_node_id,content_sha,status,last_synced)
                              VALUES (?,?,?,?) ON CONFLICT(poda_node_id) DO UPDATE SET
                              content_sha=excluded.content_sha,status=excluded.status,last_synced=excluded.last_synced""",
                           (n["id"],checksum,"synced",now_iso()))
                db.commit();synced+=1
            except Exception as exc:errors.append(f"retain {n['id']}: {type(exc).__name__}")
    finally:db.close()
    return {"enabled":True,"synchronized":synced,"deleted":deleted,"errors":errors[:8],
            "note":"PODA memory graph remains canonical. Hindsight is a local derived index only."}


def recall_node_ids(query:str,limit:int=12)->list[str]:
    if not enabled():return []
    try:
        result=_request("POST",_path("/memories/recall"),{"query":query[:1200],"budget":"low"})
        seen=[]
        for fact in result.get("results",[])[:max(1,min(limit,30))]:
            node=(fact.get("metadata") or {}).get("poda_node_id")
            if node and node not in seen:seen.append(node)
        return seen
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        return []


class ConfigRequest(BaseModel):
    enabled: bool


@router.get("/status")
def status():
    db=connect()
    try:
        ensure_tables(db)
        mapped=db.execute("SELECT COUNT(*) FROM hindsight_node_index").fetchone()[0]
    finally:db.close()
    reachable=False
    try:
        # No remote endpoints, even for health diagnostics.
        with httpx.Client(timeout=2,trust_env=False,follow_redirects=False) as c:
            reachable=c.get(BASE+"/health").status_code==200
    except httpx.HTTPError:pass
    return {"enabled":enabled(),"local_server_reachable":reachable,
            "indexed_nodes":mapped,"url":BASE,
            "warning":"Requires a separately installed LOCAL Hindsight engine with Ollama; no cloud endpoints. Hindsight keeps a derived local index, PODA's encrypted graph remains authoritative."}


@router.put("/config")
def configure(req:ConfigRequest):
    if req.enabled:
        try:
            _request("GET","/health")
        except Exception as exc:
            raise HTTPException(409,detail="Start a local Hindsight server at 127.0.0.1:8888 with Ollama before enabling") from exc
    set_setting("hindsight_local_enabled","true" if req.enabled else "false")
    return status()


@router.post("/sync")
def sync_memory(limit:int=35):
    try:return sync_once(limit)
    except httpx.HTTPError as exc:
        raise HTTPException(503,detail=f"Local Hindsight unavailable: {type(exc).__name__}") from exc


class FeedbackRequest(BaseModel):
    node_id: str
    assessment: str
    reason: str = ""


@router.post("/feedback")
def feedback(req: FeedbackRequest):
    """Record explicit quality feedback on a visible PODA memory node."""
    if req.assessment not in {"helpful","incorrect","outdated","restore"}:
        raise HTTPException(422,detail="Invalid feedback assessment")
    db=connect()
    try:
        ensure_tables(db)
        node=one(db,"SELECT id,node_type FROM memory_nodes WHERE id=?",(req.node_id,))
        if not node or node["node_type"]!="memory":
            raise HTTPException(404,detail="Visible memory node not found")
        if req.assessment=="restore":
            db.execute("DELETE FROM poda_memory_feedback WHERE poda_node_id=?",(req.node_id,))
        else:
            db.execute("""INSERT INTO poda_memory_feedback VALUES (?,?,?,?)
                      ON CONFLICT(poda_node_id) DO UPDATE SET assessment=excluded.assessment,
                      reason=excluded.reason,updated_at=excluded.updated_at""",
                      (req.node_id,req.assessment,req.reason[:600],now_iso()))
        db.commit()
    finally: db.close()
    return {"node_id":req.node_id,"assessment":req.assessment,"sync_required":enabled(),
            "note":"Source node is preserved for user review; incorrect/outdated nodes are excluded from retrieval and Hindsight at next sync."}


async def background_sync():
    """Incrementally reconcile only after explicit opt-in; never fetch from cloud."""
    import asyncio
    while True:
        try:
            if enabled():
                await asyncio.to_thread(sync_once,15)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(600)
