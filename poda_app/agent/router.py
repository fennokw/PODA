"""Agent API: folder grants, receipts. Tool execution endpoints are added by agent/tools.py integration."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime import capability
from . import grants, receipts, tools, orchestrator

router = APIRouter()


class GrantRequest(BaseModel):
    root_path: str
    mode: str = "read"
    label: str | None = None
    allow_execute: bool = False
    ttl_minutes: int | None = None


@router.get("/agent/grants")
def list_grants(include_inactive: bool = False) -> dict[str, Any]:
    return {"grants": grants.list_grants(include_inactive), "capability": grants.filesystem_capability()}


@router.post("/agent/grants")
def add_grant(req: GrantRequest) -> dict[str, Any]:
    try:
        grant = grants.add_grant(req.root_path, req.mode, req.label, req.allow_execute, req.ttl_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    capability.sync_ledger()
    return {"grant": grant, "capability": grants.filesystem_capability()}


@router.delete("/agent/grants/{grant_id}")
def revoke_grant(grant_id: str) -> dict[str, Any]:
    ok = grants.revoke_grant(grant_id)
    capability.sync_ledger()
    if not ok:
        raise HTTPException(status_code=404, detail="Grant not found or already revoked")
    return {"revoked": True, "capability": grants.filesystem_capability()}


@router.get("/agent/receipts")
def list_receipts(limit: int = 50) -> dict[str, Any]:
    return {"receipts": receipts.list_receipts(limit)}


@router.get("/agent/receipts/{receipt_id}")
def get_receipt(receipt_id: str) -> dict[str, Any]:
    r = receipts.get_receipt(receipt_id)
    if not r:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return r


class ToolRunRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    approve: bool = False
    session_id: str | None = None


@router.get("/agent/tools")
def list_tools() -> dict[str, Any]:
    return {"tools": tools.describe()}


@router.post("/agent/tools/run")
def run_tool(req: ToolRunRequest) -> dict[str, Any]:
    if req.tool not in tools.TOOLS:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {req.tool}")
    out = tools.execute(req.tool, req.args, approved=req.approve, session_id=req.session_id)
    capability.sync_ledger()
    return out


@router.get("/agent/proposals")
def list_proposals(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"proposals": orchestrator.list_proposals(status, limit)}


@router.get("/agent/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, Any]:
    prop = orchestrator.get_proposal(proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return prop


@router.post("/agent/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return orchestrator.approve(proposal_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Proposal not found")


@router.post("/agent/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return {"proposal": orchestrator.reject(proposal_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Proposal not found")
