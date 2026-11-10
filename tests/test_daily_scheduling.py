"""Regression coverage for email-derived calendar writes and the scheduling MCP."""
import asyncio
from datetime import datetime
import json
import sys
from zoneinfo import ZoneInfo

import pytest

from poda_app.chat import router as chat
from poda_app.scheduling.temporal import resolve_event_time

PROMPT = (
    "Give me a review of important emails since yesterday morning, remind me of what is on my "
    'calendar for the day, add any new scheduled events that showed up in email in the past day '
    'to my calendar in the "maybe" category under events/tasks, and lastly give me an overview on '
    "what work I have to do and where I stand on my various projects with a specific recommended "
    "amount of time to work on each project for the day based on my schedule, due dates, and priority level."
)
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 9, 9, tzinfo=NY)
MAIL = [
    {"subject": "2 events happening tomorrow", "date": "2026-11-09T13:25:00Z",
     "sent_at": "2026-11-09T13:15:00Z", "sender_timezone_known": True, "snippet": (
         "PRODUCTION WORKSHOP 8:00–8:30 PM ET\nOPEN Q&A 9:00–9:30 PM ET"
     )},
    {"subject": "Studio space", "date": "2026-11-09T04:30:00Z", "sent_at": "2026-11-09T04:00:00Z",
     "snippet": "See you in Room 204 tomorrow at 2 PM."},
    {"subject": "Re: Your Meeting is In 1 Hour!", "date": "2026-11-09T13:00:00Z",
     "sent_at": "2026-11-09T12:58:00Z", "snippet": "Thanks!\nOn Sun Nov 8, Jo wrote:\nSee you in 1 hour."},
    {"subject": "Lab time", "date": "2026-11-09T12:00:00Z", "sent_at": "2026-11-09T12:00:00Z",
     "snippet": "Can you do Tuesday at 2 PM?"},
    {"subject": "Old event", "date": "2026-11-08T12:00:00Z", "sent_at": "2026-11-08T12:00:00Z",
     "snippet": "See you Tuesday at 4 PM."},
]


def found(source, title, quote, clock, *, day="", end="", zone="", status="scheduled"):
    return dict(source=source, title=title, evidence=quote, date_text=day, time_text=clock,
                end_text=end, event_timezone=zone, status=status)


def extract(messages, **kwargs):
    rows = json.loads(messages[-1]["content"])
    assert len(rows) == 4
    assert rows[2]["body"] == "Thanks!"
    return {"message": {"content": json.dumps({"events": [
        found(0, "PRODUCTION WORKSHOP", "PRODUCTION WORKSHOP 8:00–8:30 PM ET", "8:00",
              day="today", end="8:30 PM", zone="ET"),
        found(0, "OPEN Q&A", "OPEN Q&A 9:00–9:30 PM ET", "9:00",
              day="tomorrow", end="9:30 PM", zone="ET"),
        found(1, "Gathering in Room 204", "See you in Room 204 tomorrow at 2 PM", "2 PM", day="tomorrow"),
        found(2, "Stale meeting reminder", "See you in 1 hour", "in 1 hour"),
        found(3, "Lab time", "Can you do Tuesday at 2 PM?", "2 PM", day="Tuesday", status="tentative"),
    ]})}}


def test_scheduled_prompt_keeps_mail_and_event_windows_separate(monkeypatch, client):
    from poda_app.agent import orchestrator, tools
    from poda_app.daily_updates import permissions

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    writes = []
    seen = []
    reads = []
    monkeypatch.setattr(chat, "datetime", FixedClock)
    monkeypatch.setattr(chat.capability, "sync_ledger", lambda: {"email": {"can_read": True}})
    monkeypatch.setattr(chat.models, "resolve", lambda *a: {"model": "fake", "profile": "advanced", "num_ctx": 8192, "options": {}})
    monkeypatch.setattr(orchestrator, "_planner_models", lambda *a: ["fake"])
    monkeypatch.setattr(permissions, "authorize", lambda sid, rev, name, args: (args["title"] != "Gathering in Room 204", "Not authorized"))

    def model(messages, name, **kw):
        if kw.get("format"):
            return extract(messages, **kw)
        assert all(s["function"]["name"] != "notion_calendar_create" for s in kw["tools"])
        if not seen:
            seen.append("planner")
            return {"message": {"tool_calls": [
                {"function": {"name": "notion_calendar_create", "arguments": {"title": "Guessed", "start": "2026-11-09T21:00:00Z"}}},
                {"function": {"name": "notion_agenda", "arguments": {"day": "today"}}},
            ]}}
        return {"message": {"content": "Done"}}

    def execute(name, args, *, approved, session_id):
        if name == "mail_search":
            assert args["query"] == "" and args["related_terms"] == []
            assert (args["start_date"], args["end_date"], args["date_timezone"]) == ("2026-11-08", "2026-11-09", NY.key)
            result = {"messages": MAIL, "read_bodies": len(MAIL)}
        elif name == "notion_agenda":
            assert args == {"day": "2026-11-09", "timezone_name": NY.key}
            reads.append(name)
            result = {"covers": "today = 2026-11-09"}
        elif name in {"workload_list", "project_manager_list"}:
            reads.append(name)
            result = {"count": 0}
        else:
            assert name == "notion_calendar_create" and approved
            writes.append(args)
            result = {"created": True, "verified": True, **args}
        return {"receipt": {"id": "test", "outcome": "succeeded"}, "result": {"ok": True, "result": result}}

    async def stream(messages, name, **kw):
        assert "MAIL REVIEW" in messages[0]["content"]
        yield {"message": {"content": "Daily review"}, "done": True}

    monkeypatch.setattr(chat.inference, "chat", model)
    monkeypatch.setattr(chat.inference, "chat_stream", stream)
    monkeypatch.setattr(tools, "execute", execute)

    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        response = await chat.run_chat(chat.ChatRequest(message=PROMPT, use_memory=False), Request(),
                                       ephemeral=True, unattended_target="morning", unattended_revision="1",
                                       unattended_mail_accounts=["a"], unattended_timezone=NY.key)
        return [frame async for frame in response.body_iterator]

    frames = asyncio.run(run())
    tokens = "".join(json.loads(frame.split("data: ", 1)[1])["token"]
                     for frame in frames if frame.startswith("event: token"))
    assert reads == ["notion_agenda", "workload_list", "project_manager_list"]
    assert [args["start"] for args in writes] == ["2026-11-10T20:00:00-05:00", "2026-11-10T21:00:00-05:00"]
    assert all(args["commitment"] == "Maybe" for args in writes)
    assert "2026-11-10 20:00" in tokens and "Added as **Maybe**" in tokens
    assert "Gathering in Room 204" in tokens and "2026-11-09 14:00" in tokens and "Not authorized" in tokens
    assert "For review — Lab time" in tokens and "Stale meeting reminder" not in tokens


def test_countdown_uses_original_send_time_and_dst_is_validated():
    assert resolve_event_time("2026-11-09T20:00:00-05:00", "", "in 1 hour", NY.key)["start"] == "2026-11-09T21:00:00-05:00"
    assert resolve_event_time("2026-11-10T02:00:00Z", "tomorrow", "8 PM", NY.key, event_timezone="ET",
                              sender_timezone_known=True)["start"] == "2026-11-10T20:00:00-05:00"
    with pytest.raises(ValueError, match="daylight-saving"):
        resolve_event_time("2026-03-07T12:00:00-05:00", "tomorrow", "2:30 AM", NY.key)
    assert resolve_event_time("2026-11-10T02:00:00Z", "tomorrow", "8 PM", NY.key,
                              sender_timezone_known=True)["start"] == "2026-11-11T20:00:00+00:00"


def test_mcp_stdio_lists_and_resolves_scheduling_tools():
    mcp = pytest.importorskip("mcp")
    from mcp.client.stdio import stdio_client

    async def run():
        server = mcp.StdioServerParameters(command=sys.executable, args=["-m", "poda_app.scheduling.mcp"])
        async with stdio_client(server) as (read, write), mcp.ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {"resolve_event_time", "preview_email_schedule"}
            result = await session.call_tool("resolve_event_time", {
                "sent_at": "2026-11-10T00:10:00Z", "date_text": "tomorrow", "time_text": "8 PM", "timezone_name": NY.key
            })
            assert not result.isError
            assert result.structuredContent["start"] == "2026-11-10T20:00:00-05:00"

    asyncio.run(run())
