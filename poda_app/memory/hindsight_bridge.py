"""Local-only Hindsight engine adapter synchronized to PODA's canonical graph.

PODA memory_nodes is the source of truth. Hindsight's per-node documents are
a MATERIALIZED INDEX, not a second PODA chat history or independent memory tree.
Deletion and edits reconcile by stable document_id (poda-node-<uuid>).
PODA starts and authenticates a managed local service with llama.cpp.
"""
from __future__ import annotations

import hashlib
from threading import Lock
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..runtime.db import connect, get_setting, set_setting, rows, one, now_iso
from . import hindsight_runtime

router=APIRouter(prefix="/memory/hindsight",tags=["memory"])
BANK="poda-local-graph-gguf-v1"
MAX_SYNC_PER_CALL=35
_SYNC_LOCK = Lock()


def enabled()->bool:
    return get_setting("hindsight_local_enabled","false")=="true"


def _path(suffix:str)->str:
    return f"/v1/default/banks/{BANK}"+suffix


def _request(method:str,path:str,payload:dict|None=None)->dict:
    # Fixed loopback host, no user-controllable address and no environment proxy.
    # Local llama.cpp fact extraction can exceed ordinary HTTP read budgets;
    # recall is bounded tightly so chat remains responsive on outage.
    operation_timeout = 120 if method=="POST" and path.endswith("/memories") else 8
    url, headers = hindsight_runtime.endpoint()
    with httpx.Client(base_url=url, headers=headers, timeout=operation_timeout, trust_env=False, follow_redirects=False) as client:
        response=client.request(method,path,json=payload)
        if method == "DELETE" and response.status_code == 404:
            return {}
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
    if not _SYNC_LOCK.acquire(blocking=False):
        return {"enabled": True, "synchronized": 0, "deleted": 0, "in_progress": True}
    try:
        hindsight_runtime.start()
        return _reconcile(max(1, min(int(limit), MAX_SYNC_PER_CALL)))
    finally:
        _SYNC_LOCK.release()


def _reconcile(limit: int) -> dict:
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
        attempts = 0
        for missing in sorted(set(existing)-set(current))[:limit]:
            if not enabled(): break
            attempts += 1
            try:
                _request("DELETE",_path("/documents/"+_document_id(missing)))
                db.execute("DELETE FROM hindsight_node_index WHERE poda_node_id=?",(missing,))
                db.commit();deleted+=1
            except Exception as exc:errors.append(f"delete {missing}: {type(exc).__name__}")
        for n in all_nodes:
            if attempts >= limit or not enabled(): break
            text="\n".join(x for x in (n["title"],n["summary"],n["content"]) if x)[:8000]
            checksum=hashlib.sha256((BANK+"|"+text+"|"+str(n["parent_id"])+"|"+str(n["surface_id"])).encode()).hexdigest()
            old=existing.get(n["id"])
            if old and old["content_sha"]==checksum and old["status"]=="synced":continue
            attempts += 1
            payload={"async":False,"items":[{"content":text,
                "document_id":_document_id(n["id"]), "context":"PODA canonical memory node",
                "metadata":{"poda_node_id":n["id"],"parent_id":str(n["parent_id"] or ""),
                            "surface_id":str(n["surface_id"] or ""),
                            "origin":"PODA","priority":str(n["importance"] or 0.5)}}]}
            try:
                retained = _request("POST", _path("/memories"), payload)
                if not retained.get("success"):
                    raise RuntimeError("Hindsight did not confirm the retain operation.")
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
            document_id = fact.get("document_id") or ""
            node = document_id.removeprefix("poda-node-") if document_id.startswith("poda-node-") else None
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
    return {"enabled": enabled(), "indexed_nodes": mapped, **hindsight_runtime.status()}


@router.get("/diagnostics")
def diagnostics():
    return {"log": hindsight_runtime.diagnostics()}


class SetupRequest(BaseModel):
    confirm_download: bool = False


@router.post("/setup")
def setup(req: SetupRequest, tasks: BackgroundTasks):
    if not req.confirm_download:
        raise HTTPException(400, detail="Confirm the Hindsight download to continue.")
    try:
        hindsight_runtime.install_and_start()
    except RuntimeError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if get_setting("hindsight_managed_index", "false") != "true":
        db = connect()
        try:
            ensure_tables(db)
            db.execute("DELETE FROM hindsight_node_index")
            db.commit()
        finally:
            db.close()
        set_setting("hindsight_managed_index", "true")
    set_setting("hindsight_local_enabled", "true")
    tasks.add_task(_background_reconcile)
    return status()


@router.put("/config")
def configure(req: ConfigRequest, tasks: BackgroundTasks):
    if req.enabled:
        try:
            hindsight_runtime.start()
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
    set_setting("hindsight_local_enabled", "true" if req.enabled else "false")
    if req.enabled:
        tasks.add_task(_background_reconcile)
    else:
        hindsight_runtime.stop()
    return status()


@router.post("/sync")
def sync_memory(limit:int=35):
    try:return sync_once(limit)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(503, detail=str(exc)) from exc


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


def _background_reconcile():
    try:
        sync_once(5)
    except (RuntimeError, httpx.HTTPError):
        pass


async def background_sync():
    """Incrementally reconcile only after explicit opt-in; never fetch from cloud."""
    import asyncio
    try:
        while True:
            try:
                if enabled():
                    await asyncio.to_thread(sync_once, 15)
            except Exception:
                pass
            await asyncio.sleep(60)
    finally:
        await asyncio.to_thread(hindsight_runtime.stop)
