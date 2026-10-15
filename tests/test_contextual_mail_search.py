"""Date-bounded mail search and context-gated recall regression tests."""
from __future__ import annotations

from poda_app.chat.router import should_recall_memory
from poda_app.connectors.email import service
from poda_app.connectors.email.models import MailError


def test_memory_recall_skips_live_mail_but_respects_history():
    assert not should_recall_memory("Give me a brief overview of emails from the past week.")
    assert not should_recall_memory("What can you connect to?")
    assert should_recall_memory("Remember what we discussed about my emails last time")
    assert should_recall_memory("Help me with my project design")
    assert not should_recall_memory("Remember the project", enabled=False)


def test_date_bounded_mail_search_without_keyword(monkeypatch):
    account = {"id": "a1", "provider": "gmail_imap", "account_name": "Gmail", "address": "test@example.com"}
    monkeypatch.setattr(service.state, "list_accounts", lambda: [account])
    monkeypatch.setattr(service, "_require_read", lambda a: None)
    monkeypatch.setattr(service, "_touch", lambda *args: None)
    rows = [
        {"id": "new", "account_id": "a1", "date": "2026-10-14T12:00:00+00:00", "subject": "Meeting", "from_name": "Alice", "message_id": "m1"},
        {"id": "old", "account_id": "a1", "date": "2026-09-20T12:00:00+00:00", "subject": "Older", "from_name": "Bob", "message_id": "m2"},
    ]
    calls = []
    def fake_list(address, aid, mailbox, limit=50, offset=0, query=None):
        calls.append((mailbox, query))
        return {"messages": rows if offset == 0 else [], "total": 2}
    monkeypatch.setattr(service.gmail_imap, "list_messages", fake_list)
    out = service.search("", start_date="2026-10-08", end_date="2026-10-15")
    assert [x["id"] for x in out["messages"]] == ["new"]
    assert out["matched_candidates"] == 1
    assert all(query is None for _, query in calls)
    assert len(calls) == 2


def test_semantic_expansion_ranks_related_subjects(monkeypatch):
    account = {"id": "a1", "provider": "gmail_imap", "account_name": "Gmail", "address": "test@example.com"}
    monkeypatch.setattr(service.state, "list_accounts", lambda: [account])
    monkeypatch.setattr(service, "_require_read", lambda a: None)
    monkeypatch.setattr(service, "_touch", lambda *args: None)
    def fake_list(address, aid, mailbox, limit=50, offset=0, query=None):
        return {"messages": [{"id": query + mailbox, "account_id": aid, "date": "2026-10-14T12:00:00+00:00",
                              "subject": query.title(), "from_name": "Sender", "message_id": query}],
                "total": 1}
    monkeypatch.setattr(service.gmail_imap, "list_messages", fake_list)
    out = service.search("student funding", related_terms=["scholarship", "financial aid"])
    assert out["matched_candidates"] == 3
    assert len(out["messages"]) == 3
    assert "scholarship" in out["related_terms"]


def test_bad_date_rejected():
    try:
        service.search("", start_date="last tuesday")
    except MailError as exc:
        assert "YYYY-MM-DD" in exc.message
    else:
        raise AssertionError("Invalid date silently accepted")
