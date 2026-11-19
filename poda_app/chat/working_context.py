"""Cheap, bounded working context for the active chat session.

This is a *view* over the existing encrypted messages table, not a new persistent
shadow memory store. Turns age out after 12 messages and remain available as
visible second-brain memories through the existing ingestion/compaction pipeline.
"""
from __future__ import annotations

from ..runtime.db import connect, rows
from .artifacts import is_attachment_retry


def request_before_retry(session_id: str) -> str | None:
    """Read the latest substantive user turn, without treating assistant prose as an instruction."""
    db = connect()
    try:
        turns = rows(db, """SELECT content FROM messages WHERE session_id=? AND role='user'
                            ORDER BY rowid DESC LIMIT 12""", (session_id,))
    finally:
        db.close()
    return next((turn["content"] for turn in turns if turn["content"] and not is_attachment_retry(turn["content"])), None)


def recent(session_id: str | None, exclude_message_id: str | None = None,
           max_messages: int = 12, per_message: int = 950) -> str:
    if not session_id:
        return "[new session]"
    db = connect()
    try:
        found = rows(db, """SELECT id, role, content FROM messages
                            WHERE session_id=? AND role IN ('user', 'assistant')
                              AND (? IS NULL OR id != ?)
                            ORDER BY rowid DESC LIMIT ?""",
                     (session_id, exclude_message_id, exclude_message_id, max(1, min(max_messages, 24))))
    finally:
        db.close()
    entries = []
    for item in reversed(found):
        role = item["role"]
        content = (item.get("content") or "").strip()
        if content:
            entries.append(f"{role}: {content[:per_message]}")
    return "\n\n".join(entries) or "[no previous turns in this session]"


def for_request(session_id: str | None, exclude_message_id: str | None,
                query: str, work_discussion: bool) -> str:
    if not work_discussion or not session_id:
        return recent(session_id, exclude_message_id)
    import re
    from collections import Counter

    db = connect()
    try:
        messages = rows(db, """SELECT id, role, content FROM messages
            WHERE session_id=? AND (? IS NULL OR id!=?) ORDER BY rowid""",
            (session_id, exclude_message_id, exclude_message_id))
    finally:
        db.close()
    if not messages:
        return "[no previous turns in this discussion]"
    if sum(len(turn["content"]) + 15 for turn in messages) <= 12000:
        return "\n\n".join(f'{turn["role"]}: {turn["content"]}' for turn in messages)
    latest = messages[-8:]
    terms = set(re.findall(r"[a-z0-9_.-]{4,}", query.lower()))
    scores = []
    for index, turn in enumerate(messages[:-8]):
        words = Counter(re.findall(r"[a-z0-9_.-]{4,}", turn["content"].lower()))
        score = sum(min(2, words[t]) for t in terms)
        if score or turn["role"] == "user" and index < 2:
            scores.append((score, index))
    matches = sorted(index for _, index in sorted(scores, reverse=True)[:5])
    blocks = []
    if matches:
        blocks.append("Earlier excerpts from this Work Discussion:")
        blocks.extend(f'{messages[i]["role"]}: {messages[i]["content"][:740]}' for i in matches)
    blocks.append("Latest turns from this Work Discussion:")
    blocks.extend(f'{turn["role"]}: {turn["content"][:950]}' for turn in latest)
    return "\n\n".join(blocks)
