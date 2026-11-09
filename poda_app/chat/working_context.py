"""Cheap, bounded working context for the active chat session.

This is a *view* over the existing encrypted messages table, not a new persistent
shadow memory store. Turns age out after 12 messages and remain available as
visible second-brain memories through the existing ingestion/compaction pipeline.
"""
from __future__ import annotations

from ..runtime.db import connect, rows


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
