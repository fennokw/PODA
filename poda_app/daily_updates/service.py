"""Encrypted SQLite-backed local scheduled prompts.

Run only while the PODA process is alive. Exactly-once slot claims survive
restart; a 10-minute grace window permits delayed wakeups without replaying
all missed days. No network scheduler or cloud push service.
"""
from __future__ import annotations

import asyncio
import json
import platform
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..runtime.db import connect, now_iso, one, rows, add_column

MAX_GRACE_SECONDS = 600
_RUNNING: set[str] = set()
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_prompts (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
        times_json TEXT NOT NULL, timezone TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_prompt_runs (
        id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL, slot_key TEXT NOT NULL,
        scheduled_for TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        status TEXT NOT NULL, response TEXT NOT NULL DEFAULT '', error TEXT,
        notified INTEGER NOT NULL DEFAULT 0,
        UNIQUE(prompt_id, slot_key))""")
    add_column(conn, "daily_prompts", "calendar_write_target", "TEXT")
    add_column(conn, "daily_prompts", "permissions_json", "TEXT NOT NULL DEFAULT '[]'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_runs_recent ON daily_prompt_runs(scheduled_for DESC)")


def clean_times(times: list[str]) -> list[str]:
    if not isinstance(times,list) or not 1 <= len(times) <= 12:
        raise ValueError("Choose one to twelve daily times")
    values = sorted(set(times))
    if len(values) != len(times) or not all(isinstance(v,str) and _TIME.fullmatch(v) for v in values):
        raise ValueError("Use unique local times HH:MM (24-hour format)")
    return values


def clean_timezone(value: str) -> str:
    if not isinstance(value,str) or len(value)>80:
        raise ValueError("Invalid timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError,KeyError) as exc:
        raise ValueError("Select a valid IANA timezone such as America/New_York") from exc
    return value


def tasks() -> list[dict[str,Any]]:
    db=connect()
    try:
        ensure_tables(db)
        out=rows(db,"SELECT * FROM daily_prompts ORDER BY updated_at DESC")
        return [{**row,"times":json.loads(row["times_json"]), "enabled":bool(row["enabled"])}
                for row in out]
    finally: db.close()


def save(title:str,prompt:str,times:list[str],timezone_name:str,enabled:bool=True,id:str|None=None,
         permissions:list[dict[str,Any]]|None=None) -> dict[str,Any]:
    title=title.strip()
    prompt=prompt.strip()
    if not 1 <= len(title) <= 120 or not 1 <= len(prompt) <= 12000:
        raise ValueError("Title must be 1-120 chars; prompt must be 1-12000 chars")
    clean=clean_times(times); tz=clean_timezone(timezone_name); stamp=now_iso()
    from .permissions import prepare
    authorized = prepare(permissions or [])
    db=connect()
    try:
        ensure_tables(db)
        if id:
            if not one(db,"SELECT id FROM daily_prompts WHERE id=?",(id,)):
                raise KeyError("Daily prompt not found")
            db.execute("UPDATE daily_prompts SET title=?,prompt=?,times_json=?,timezone=?,enabled=?,updated_at=?,calendar_write_target=?,permissions_json=? WHERE id=?",
                       (title,prompt,json.dumps(clean),tz,int(bool(enabled)),stamp,None,json.dumps(authorized),id))
        else:
            id=str(uuid.uuid4())
            db.execute("""INSERT INTO daily_prompts
                      (id,title,prompt,times_json,timezone,enabled,created_at,updated_at,calendar_write_target,permissions_json)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (id,title,prompt,json.dumps(clean),tz,int(bool(enabled)),stamp,stamp,None,json.dumps(authorized)))
        db.commit()
        return {**one(db,"SELECT * FROM daily_prompts WHERE id=?",(id,)), "times":clean,"enabled":bool(enabled)}
    finally: db.close()


def remove(id:str) -> bool:
    db=connect()
    try:
        ensure_tables(db)
        # History is preserved for auditing after a schedule is removed.
        removed=db.execute("DELETE FROM daily_prompts WHERE id=?",(id,)).rowcount
        db.commit()
        return bool(removed)
    finally:db.close()


def history(limit:int=100):
    db=connect()
    try:
        ensure_tables(db)
        return rows(db,"""SELECT r.*, COALESCE(p.title,'Deleted schedule') AS title
            FROM daily_prompt_runs r LEFT JOIN daily_prompts p ON p.id=r.prompt_id
            ORDER BY r.scheduled_for DESC LIMIT ?""",(max(1,min(limit,300)),))
    finally: db.close()


def _slots(row:dict[str,Any],now:datetime) -> list[tuple[str,datetime]]:
    tz=ZoneInfo(row["timezone"])
    local=now.astimezone(tz)
    output=[]
    for day in (local.date()-timedelta(days=1),local.date()):
        for clock in json.loads(row["times_json"]):
            hour,minute=map(int,clock.split(":"))
            planned=datetime(day.year,day.month,day.day,hour,minute,tzinfo=tz)
            # Skip nonexistent DST times; on fall-back use first occurrence.
            utc=planned.astimezone(timezone.utc)
            if utc.astimezone(tz).strftime("%Y-%m-%d %H:%M") != f"{day.isoformat()} {clock}":
                continue
            age=(now-utc).total_seconds()
            if 0 <= age <= MAX_GRACE_SECONDS:
                output.append((f"{day.isoformat()}@{clock}",utc))
    return output


def claim_due(now:datetime|None=None) -> list[dict[str,Any]]:
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("now must include timezone")
    db=connect()
    new=[]
    try:
        ensure_tables(db)
        for task in rows(db,"SELECT * FROM daily_prompts WHERE enabled=1"):
            for slot,utc in _slots(task,now):
                id=str(uuid.uuid4())
                rc=db.execute("""INSERT OR IGNORE INTO daily_prompt_runs
                      (id,prompt_id,slot_key,scheduled_for,status)
                      VALUES (?,?,?,?,'queued')""",(id,task["id"],slot,utc.isoformat()))
                if rc.rowcount:
                    new.append({"run_id":id,"task":task,"slot":slot})
        db.commit()
        return new
    finally:db.close()


def mark(run_id:str,status:str,response:str="",error:str|None=None,notified:bool=False):
    db=connect()
    try:
        ensure_tables(db)
        if status=="running":
            db.execute("UPDATE daily_prompt_runs SET status='running',started_at=? WHERE id=?",
                       (now_iso(),run_id))
        else:
            db.execute("""UPDATE daily_prompt_runs SET status=?, response=?, error=?,
                    notified=?,finished_at=? WHERE id=?""",
                       (status,response[:100000],(error or "")[:2000] or None,int(notified),now_iso(),run_id))
        db.commit()
    finally: db.close()


def _notify(title:str,body:str)->bool:
    if platform.system()!="Darwin":
        return False
    def quote(s):
        return '"' + s.replace("\\","\\\\").replace('"','\\"').replace("\n"," ")[:240] + '"'
    script=f"display notification {quote(body)} with title {quote('PODA Daily Automation')} subtitle {quote(title)}"
    try:
        result=subprocess.run(["osascript","-e",script],capture_output=True,text=True,timeout=12)
        return result.returncode==0
    except (OSError,subprocess.TimeoutExpired):
        return False


async def execute(run_id:str,task:dict[str,Any]):
    from ..chat.router import run_chat, ChatRequest
    class BackgroundRequest:
        async def is_disconnected(self): return False
    _RUNNING.add(run_id)
    mark(run_id,"running")
    captured=[]
    error=None
    session_id="daily-"+run_id
    try:
        # The same chat tools and model are used, but messages never enter
        # chat history, working context, summaries or the second-brain graph.
        # Only the explicitly authorized calendar target may be mutated.
        req=ChatRequest(message=task["prompt"],session_id=session_id,
                        mode="auto",thinking_level="balanced",use_memory=False)
        result=await run_chat(req,BackgroundRequest(),ephemeral=True,
                              unattended_target=task.get("id"), unattended_revision=task.get("updated_at"))
        async def collect():
            nonlocal error
            async for frame in result.body_iterator:
                if isinstance(frame,bytes): frame=frame.decode("utf-8","replace")
                kind=None;payload={}
                for line in frame.splitlines():
                    if line.startswith("event:"): kind=line[6:].strip()
                    if line.startswith("data:"):
                        try: payload=json.loads(line[5:].strip())
                        except (ValueError,TypeError): pass
                if kind=="token": captured.append(str(payload.get("token") or ""))
                if kind=="error": error=str(payload.get("message") or "Generation failed")
                if kind=="proposal":
                    captured.append("\n\n[An action needs approval in the interactive chat and was NOT performed.]")
        await asyncio.wait_for(collect(),timeout=900)
        output="".join(captured).strip()
        if error: mark(run_id,"failed",output,error)
        elif not output: mark(run_id,"failed","","No response was generated")
        else:
            success=_notify(task["title"],output[:190])
            mark(run_id,"completed",output,notified=success)
    except asyncio.CancelledError:
        mark(run_id,"interrupted","".join(captured),"The local PODA server stopped")
        raise
    except Exception as exc:
        mark(run_id,"failed","".join(captured),f"{type(exc).__name__}: {exc}")
    finally:
        _RUNNING.discard(run_id)


async def scheduler():
    """PODA's process-owned loop; schedules do not run while PODA is shut down."""
    while True:
        try:
            due=await asyncio.to_thread(claim_due)
            for item in due:
                asyncio.create_task(execute(item["run_id"],item["task"]),name="poda-daily-"+item["run_id"][:8])
        except asyncio.CancelledError: raise
        except Exception as exc:
            print(f"[PODA daily scheduler] {type(exc).__name__}: {exc}")
        await asyncio.sleep(20)
