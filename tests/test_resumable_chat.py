"""Resumable chat run lifecycle: generation must not belong to a viewer connection."""
from __future__ import annotations

import asyncio

from fastapi.responses import StreamingResponse

from poda_app.chat import runs
from poda_app.chat.router import ChatRequest, sse


def test_background_chat_continues_without_stream_subscriber(monkeypatch):
    async def fake_stream(req, request):
        async def chunks():
            yield sse("session", {"session_id": req.session_id})
            yield sse("token", {"token": "A"})
            await asyncio.sleep(0.03)
            yield sse("token", {"token": "B"})
            yield sse("done", {"model": "test-model"})
        return StreamingResponse(chunks())

    monkeypatch.setattr(runs, "chat_stream", fake_stream)

    async def check():
        created = await runs.create_run(ChatRequest(message="Run without attached browser"))
        obj = runs._lookup(created["run_id"])
        assert obj.state == "running"
        # No GET subscriber exists. The server still generates all events.
        await asyncio.sleep(0.08)
        assert obj.state == "completed"
        frames = "".join(payload for _, payload in obj.events)
        assert '"token": "A"' in frames
        assert '"token": "B"' in frames
        assert "event: done" in frames
        assert obj.next_id >= 5
    asyncio.run(check())


def test_cancel_stops_server_run(monkeypatch):
    async def fake_stream(req, request):
        async def chunks():
            yield sse("token", {"token": "started"})
            await asyncio.sleep(2)
            yield sse("done", {})
        return StreamingResponse(chunks())
    monkeypatch.setattr(runs, "chat_stream", fake_stream)

    async def check():
        created = await runs.create_run(ChatRequest(message="Cancel my generation"))
        await asyncio.sleep(0.02)
        result = await runs.cancel(created["run_id"])
        assert result["state"] == "cancelled"
        assert runs._lookup(created["run_id"]).state == "cancelled"
    asyncio.run(check())
