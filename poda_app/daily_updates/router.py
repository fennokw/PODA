"""Manage recurring personal prompts; results are stored locally and surfaced in the UI."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import service
from ..runtime.db import connect

router=APIRouter(prefix="/daily-updates",tags=["daily-updates"])

class DailyPrompt(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    prompt: str = Field(min_length=1,max_length=12000)
    times: list[str] = Field(min_length=1,max_length=12)
    timezone: str = Field(min_length=2,max_length=80)
    enabled: bool = True
    permissions: list[dict[str,Any]] = []
    mail_account_id: str | None = None

def _save(data:DailyPrompt,id:str|None=None):
    try:return service.save(data.title,data.prompt,data.times,data.timezone,data.enabled,id,
                            permissions=data.permissions,mail_account_id=data.mail_account_id)
    except KeyError as exc:raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc

@router.get("/permissions/catalog")
def permission_catalog():
    from .permissions import catalog
    from ..connectors.email import state as email_state
    return {"permissions":catalog(),
            "mail_accounts":[email_state.public_account(a) for a in email_state.list_accounts()]}

@router.get("")
def list_updates():
    return {"schedules":service.tasks(),"runs":service.history(60),
            "scheduler":"runs while PODA is open; missed times more than 10 minutes ago are not replayed",
            "notifications":"macOS notifications require permissions for the local PODA process"}

@router.post("")
def create(data:DailyPrompt):
    return {"schedule":_save(data)}

@router.put("/{id}")
def update(id:str,data:DailyPrompt):
    return {"schedule":_save(data,id)}

@router.delete("/{id}")
def remove(id:str):
    if not service.remove(id):
        raise HTTPException(status_code=404,detail="Daily prompt not found")
    return {"deleted":True,"history_preserved":True}

@router.post("/{id}/run")
async def run_now(id:str):
    task=next((t for t in service.tasks() if t["id"]==id),None)
    if not task:raise HTTPException(status_code=404,detail="Daily prompt not found")
    if service.is_running(id):
        raise HTTPException(status_code=409,detail="This automation is already running. Check its run history.")
    run_id=str(uuid.uuid4())
    db=connect()
    try:
        service.ensure_tables(db)
        db.execute("""INSERT INTO daily_prompt_runs (id,prompt_id,slot_key,scheduled_for,status)
                      VALUES (?,?,?,?, 'queued')""",
                   (run_id,id,"manual:"+run_id,datetime.now(timezone.utc).isoformat()))
        db.commit()
    finally:db.close()
    asyncio.create_task(service.execute(run_id,task),name="poda-daily-"+run_id[:8])
    return {"run_id":run_id,"state":"queued"}

@router.get("/runs/{run_id}")
def result(run_id:str):
    runs=service.history(300)
    match=next((r for r in runs if r["id"]==run_id),None)
    if not match:raise HTTPException(status_code=404,detail="Daily update run not found")
    return {"run":match}
