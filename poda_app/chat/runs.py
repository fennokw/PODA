"""Server-owned chat generation with resumable, replayable streams.

Opening another PODA screen disconnects only the stream subscriber. The Ollama
generation task belongs to this server, not to the browser request lifecycle.
Running tasks are ephemeral across process restarts, while finished chat messages
remain in PODA's encrypted history/memory tables.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .router import ChatRequest, chat_stream, sse
from ..memory import store

router = APIRouter(prefix="/chat/runs", tags=["chat"])
MAX_ACTIVE = 2
RUN_RETENTION_SECONDS = 3600
MAX_EVENT_COUNT = 20000


@dataclass
class ChatRun:
    run_id: str
    started: float
    events: list[tuple[int, str]] = field(default_factory=list)
    state: str = "running"
    next_id: int = 1
    task: asyncio.Task | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    partial: list[str] = field(default_factory=list)

    async def append(self, frame: str) -> None:
        if not frame:
            return
        async with self.condition:
            event_id = self.next_id
            self.next_id += 1
            self.events.append((event_id, frame))
            if len(self.events) > MAX_EVENT_COUNT:
                del self.events[:len(self.events) - MAX_EVENT_COUNT]
            if frame.startswith("event: token"):
                try:
                    data = json.loads(next(line[5:].strip() for line in frame.splitlines() if line.startswith("data:")))
                    self.partial.append(str(data.get("token") or ""))
                except (ValueError, StopIteration, TypeError):
                    pass
            self.condition.notify_all()


_runs: dict[str, ChatRun] = {}


def _lookup(run_id: str) -> ChatRun:
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="This run was not found; the PODA server may have restarted.")
    return run


async def _generate(run: ChatRun, req: ChatRequest) -> None:
    class PersistentRequest:
        async def is_disconnected(self) -> bool:
            return False

    try:
        response = await chat_stream(req, PersistentRequest())  # type: ignore[arg-type]
        async for frame in response.body_iterator:
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8", errors="replace")
            if not frame:
                continue
            await run.append(frame)
        if run.state == "running":
            run.state = "completed"
    except asyncio.CancelledError:
        run.state = "cancelled"
        partial = "".join(run.partial).strip()
        if partial:
            try:
                await asyncio.to_thread(store.save_message, "assistant", partial + "\n\n[response stopped by user]",
                                        "cancelled", req.session_id)
            except Exception:
                pass
        await run.append(sse("status", {"message": "cancelled"}))
        raise
    except Exception as exc:
        run.state = "failed"
        await run.append(sse("error", {"message": f"Chat run failed: {type(exc).__name__}: {exc}"}))
    finally:
        async with run.condition:
            run.condition.notify_all()


def _clean() -> None:
    cutoff = time.monotonic() - RUN_RETENTION_SECONDS
    for key, run in list(_runs.items()):
        if run.state != "running" and run.started < cutoff:
            _runs.pop(key, None)


@router.post("")
async def create_run(req: ChatRequest) -> dict[str, Any]:
    _clean()
    if sum(run.state == "running" for run in _runs.values()) >= MAX_ACTIVE:
        raise HTTPException(status_code=429, detail="Two PODA responses are already running. Stop or wait for one.")
    if not req.message.strip() or len(req.message) > 30000:
        raise HTTPException(status_code=422, detail="message must contain 1–30,000 characters")
    # Assign session ID at run creation so cancellation and reconnect share the same session.
    if not req.session_id:
        req = req.model_copy(update={"session_id": str(uuid.uuid4())})
    run = ChatRun(run_id=str(uuid.uuid4()), started=time.monotonic())
    _runs[run.run_id] = run
    run.task = asyncio.create_task(_generate(run, req), name="poda-chat-" + run.run_id[:8])
    return {"run_id": run.run_id, "session_id": req.session_id, "state": run.state}


@router.get("/{run_id}")
def status(run_id: str) -> dict[str, Any]:
    run = _lookup(run_id)
    return {"run_id": run_id, "state": run.state, "last_event_id": run.next_id - 1,
            "retained_from": run.events[0][0] if run.events else 1}


@router.get("/{run_id}/stream")
async def subscribe(run_id: str, request: Request, after: int = 0):
    run = _lookup(run_id)
    if after < 0:
        raise HTTPException(status_code=422, detail="after must be non-negative")
    try:
        last_header = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        last_header = 0
    cursor = max(after, last_header)

    async def events():
        nonlocal cursor
        while True:
            # Replaying event frames is idempotent if clients pass their last event ID.
            available = [(n, frame) for n, frame in run.events if n > cursor]
            for n, frame in available:
                cursor = n
                yield f"id: {n}\n" + frame
            if run.state != "running":
                break
            if await request.is_disconnected():
                break
            try:
                async with run.condition:
                    await asyncio.wait_for(run.condition.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/{run_id}/cancel")
async def cancel(run_id: str) -> dict[str, Any]:
    run = _lookup(run_id)
    if run.state == "running" and run.task:
        run.task.cancel()
        try:
            await asyncio.wait_for(run.task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    return {"run_id": run_id, "state": run.state}
