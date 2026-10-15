"""Live mail research must be date-aware, receipted, and independent of unrelated memory."""
from __future__ import annotations

from datetime import date

import pytest

from poda_app.chat.context_policy import is_mail_read_request, should_recall
from poda_app.connectors.email import intelligence, service
from poda_app.connectors.email.models import MailError


def test_mail_intent_vs_memory_intent():
    msg = "Just connected my email. Give me a brief overview of emails from the past week."
    assert is_mail_read_request(msg)
    assert not should_recall(msg)
    assert not should_recall("What are my calendar appointments tomorrow?")
    assert not should_recall("What is Python?")
    assert should_recall("What did we discuss last time about PODA?")
    assert should_recall("Find the email from the professor we discussed yesterday.")
    assert not is_mail_read_request("Send an email to my professor")
    assert not is_mail_read_request("Can you access my email?")


def test_natural_language_dates_and_validation():
    today = date(2026, 10, 15)
    assert intelligence.date_window("emails from the past week", today=today) == ("2026-10-08", "2026-10-16")
    assert intelligence.date_window("emails from last week", today=today) == ("2026-10-05", "2026-10-12")
    assert intelligence.date_window("emails yesterday", today=today) == ("2026-10-14", "2026-10-15")
    assert intelligence.date_window("emails this week", today=today) == ("2026-10-12", "2026-10-16")
    assert intelligence.date_window("emails between 2026-09-01 and 2026-09-15", today=today) == ("2026-09-01", "2026-09-16")
    assert intelligence.date_window("emails since 2026-01-01", today=today) == ("2026-01-01", None)
    assert intelligence.date_window("emails", after="2026-10-01", before="2026-10-16", today=today) == ("2026-10-01", "2026-10-16")
    with pytest.raises(MailError):
        intelligence.date_window("emails", after="10/01/26")


def test_mail_topic_removes_only_command_and_dates():
    msg = "Just connected my email. Give me a brief overview of emails from the past week."
    assert intelligence.extract_topic(msg) == ""
    assert intelligence.extract_topic("Find emails about robotics internships in the past 30 days") == "robotics internships"


def test_research_queries_date_and_ranks_actual_messages(monkeypatch):
    seen = {}
    monkeypatch.setattr(intelligence.ollama, "status", lambda: {"models": []})

    def fake_search(query, account_id, limit, after=None, before=None, terms=None):
        seen.update(query=query, limit=limit, after=after, before=before, terms=terms)
        return {"messages": [
            {"id": "1", "account_id": "acct", "date": "2026-10-13T10:00:00-04:00",
             "subject": "Restaurant receipt", "from_name": "Store", "snippet": ""},
            {"id": "2", "account_id": "acct", "date": "2026-10-12T10:00:00-04:00",
             "subject": "Robotics internship application update", "from_name": "Recruiting", "snippet": ""},
        ], "errors": []}

    monkeypatch.setattr(service, "search", fake_search)
    monkeypatch.setattr(service, "snippets", lambda aid, mids: {"2": "Your application moved to interview review."})
    out = intelligence.research("Find emails about robotics internships in the past week", limit=10)
    assert out["ok"]
    assert out["result"]["messages"][0]["id"] == "2"
    assert out["result"]["messages"][0]["relevance_score"] > out["result"]["messages"][1]["relevance_score"]
    assert "robotics" in seen["terms"]
    assert seen["after"] is not None and seen["before"] is not None


def test_service_search_rejects_unverified_dates(monkeypatch):
    from poda_app.connectors.email import state
    monkeypatch.setattr(state, "list_accounts", lambda: [
        {"id": "acct", "provider": "apple_mail", "account_name": "Test"}
    ])
    monkeypatch.setattr(state, "apple_status_cached", lambda: {"authorized": True})
    monkeypatch.setattr(service, "_touch", lambda *a, **k: None)
    monkeypatch.setattr(service.apple_mail, "mailboxes", lambda *_: [{"name": "INBOX"}])
    monkeypatch.setattr(service.apple_mail, "archive_target", lambda *_: "Archive")
    calls = []

    def fake_list(*args, **kwargs):
        calls.append(kwargs)
        return {"messages": [
            {"id": "new", "account_id": "acct", "message_id": "<new>", "date": "2026-10-10T12:00:00-04:00", "subject": "Recent"},
            {"id": "old", "account_id": "acct", "message_id": "<old>", "date": "2026-09-10T12:00:00-04:00", "subject": "Old"},
            {"id": "missing", "account_id": "acct", "message_id": "<missing>", "date": None, "subject": "Unknown"},
        ]}

    monkeypatch.setattr(service.apple_mail, "list_messages", fake_list)
    out = service.search("", limit=20, after="2026-10-01", before="2026-10-16")
    assert [r["id"] for r in out["messages"]] == ["new"]
    assert calls[0]["after"] == "2026-10-01" and calls[0]["before"] == "2026-10-16"
    with pytest.raises(MailError):
        service.search("", after="2026-10-20", before="2026-10-01")


def test_live_mail_chat_does_not_recall_unrelated_memory(client, monkeypatch):
    from poda_app.chat import router
    from poda_app.agent import gate

    monkeypatch.setattr(router.capability, "sync_ledger", lambda: {
        "email": {"can_read": True, "accounts": [{"account_name": "Google", "address_masked": "a***@test.local"}]},
        "notion": {"connected": False}, "filesystem": {"granted_roots": []}
    })
    monkeypatch.setattr(router.models, "resolve", lambda *args, **kw: {
        "model": "llama3.2:3b", "profile": "advanced", "num_ctx": 8192, "options": {}, "think": False
    })
    monkeypatch.setattr(router.retrieval, "recall", lambda *a, **k: pytest.fail("Irrelevant second-brain recall"))
    monkeypatch.setattr(gate, "unsupported_action_response", lambda *a: None)

    tool_calls = []
    def fake_execute(name, args, approved=False, session_id=None):
        tool_calls.append((name, args))
        return {
            "receipt": {"id": "receipt-mail", "outcome": "succeeded", "verification": "live mail read"},
            "result": {"ok": True, "result": {
                "after_inclusive": "2026-10-08", "before_exclusive": "2026-10-16",
                "messages": [{"subject": "Club meeting", "date": "2026-10-13"}],
                "errors": []}}
        }
    monkeypatch.setattr(router.agent_tools, "execute", fake_execute)

    async def fake_stream(messages, model, options=None, tools=None, think=None, timeout=900):
        assert "VERIFIED LIVE MAIL SEARCH" in messages[0]["content"]
        assert "Club meeting" in messages[0]["content"]
        yield {"message": {"content": "One verified email was found."}, "done": True}
    monkeypatch.setattr(router.ollama, "chat_stream", fake_stream)

    response = client.post("/chat/stream", json={
        "message": "Just connected my email. Give me a brief overview of emails from the past week."
    })
    assert response.status_code == 200
    assert tool_calls and tool_calls[0][0] == "mail_research"
    assert "event: tool" in response.text and "event: recall" not in response.text
    assert "One verified email" in response.text
