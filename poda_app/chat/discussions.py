"""Persistent discussions and the bounded cache used by ordinary Chat."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from ..runtime.db import connect, now_iso, one, rows
from ..memory import store

ORDINARY = "ordinary-chat"
WORK = "work-discussion"
CACHE_MESSAGES = 12


def is_work(db, session_id: str | None) -> bool:
    if not session_id:
        return False
    return bool(one(db, """SELECT 1 FROM chat_discussions WHERE session_id=?
        UNION ALL SELECT 1 FROM messages WHERE session_id=? AND provenance IN ('chat', 'work-discussion')
        LIMIT 1""", (session_id, session_id)))


def promote(db, session_id: str) -> None:
    db.execute("INSERT OR IGNORE INTO chat_discussions(session_id, created_at) VALUES (?,?)",
               (session_id, now_iso()))
    db.execute("UPDATE messages SET provenance=? WHERE session_id=? AND provenance=?",
               (WORK, session_id, ORDINARY))


def provenance(session_id: str) -> str:
    with closing(connect()) as db, db:
        return WORK if is_work(db, session_id) else ORDINARY


def open_work(session_id: str) -> None:
    with closing(connect()) as db, db:
        promote(db, session_id)


def archive(session_id: str, *, clear: bool = False) -> dict[str, int]:
    result = store.flush_session_to_graph(
        session_id, retain_messages=0, prune_ordinary=True,
        cache_messages=0 if clear else CACHE_MESSAGES,
    )
    if clear:
        with closing(connect()) as db, db:
            if not is_work(db, session_id):
                db.execute("DELETE FROM chat_materials WHERE session_id=?", (session_id,))
    return result


def clear_old_caches() -> None:
    with closing(connect()) as db, db:
        sessions = rows(db, "SELECT DISTINCT session_id FROM messages WHERE provenance=?", (ORDINARY,))
    for session in sessions:
        if session["session_id"]:
            archive(session["session_id"], clear=True)
    expire_materials()


def list_work(db):
    return rows(db, """WITH work AS (
        SELECT session_id, created_at FROM chat_discussions
        UNION ALL SELECT session_id, MIN(created_at) FROM messages
          WHERE session_id IS NOT NULL AND provenance IN ('chat','work-discussion')
          GROUP BY session_id
    ), sessions AS (SELECT session_id, MIN(created_at) AS created_at FROM work GROUP BY session_id)
    SELECT s.session_id,
        COALESCE((SELECT MAX(created_at) FROM messages WHERE session_id=s.session_id), s.created_at) AS updated_at,
        COALESCE(NULLIF(d.title,''), (SELECT substr(content,1,100) FROM messages
          WHERE session_id=s.session_id AND role='user' AND lower(trim(content))!='/work' ORDER BY rowid LIMIT 1), 'New work discussion') AS label
    FROM sessions s LEFT JOIN chat_discussions d ON d.session_id=s.session_id
    ORDER BY updated_at DESC, s.session_id LIMIT 100""")


def last_mode(session_id: str) -> str:
    with closing(connect()) as db, db:
        record = one(db, """SELECT json_extract(metadata_json, '$.discussion_mode') AS mode
            FROM messages WHERE session_id=? AND role='user' ORDER BY rowid DESC LIMIT 1""", (session_id,))
    return (record or {}).get("mode") or ""

def save_materials(session_id, files):
    with closing(connect()) as db, db:
        for file in files:
            db.execute("""INSERT OR IGNORE INTO chat_materials
                (id,session_id,name,data,created_at,digest) VALUES (?,?,?,?,?,?)""",
                (str(uuid4()), session_id, file.name, file.data, now_iso(), sha256(file.data).hexdigest()))
        if not is_work(db, session_id):
            db.execute("""DELETE FROM chat_materials WHERE session_id=? AND id NOT IN (
                SELECT id FROM chat_materials WHERE session_id=? ORDER BY rowid DESC LIMIT 4)""",
                (session_id, session_id))


def expire_materials():
    with closing(connect()) as db, db:
        db.execute("""DELETE FROM chat_materials WHERE created_at<? AND session_id NOT IN
            (SELECT session_id FROM chat_discussions) AND session_id NOT IN (
            SELECT session_id FROM messages WHERE session_id IS NOT NULL AND provenance IN ('chat','work-discussion'))""",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))


def recent_materials(session_id):
    from .inputs import InputFile, MAX_TOTAL_BYTES

    with closing(connect()) as db:
        records = rows(db, """SELECT name,data FROM chat_materials
            WHERE session_id=? ORDER BY rowid DESC LIMIT 4""", (session_id,))
    remaining = MAX_TOTAL_BYTES
    files = []
    for record in records:
        data = bytes(record["data"])
        if len(data) <= remaining:
            files.append(InputFile(record["name"], data))
            remaining -= len(data)
    return tuple(reversed(files))
