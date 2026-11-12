"""Local, read-only Messages database adapter and scoped Messages.app sender."""
from __future__ import annotations

from contextlib import closing
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import time
from urllib.parse import quote

from .typedstream_reader import try_parse_attributed_body

DB_PATH = Path.home() / 'Library' / 'Messages' / 'chat.db'


def _connect() -> sqlite3.Connection:
    if sys.platform != 'darwin':
        raise RuntimeError('Apple Messages is available on macOS only.')
    if not DB_PATH.is_file():
        raise RuntimeError('Messages history is unavailable. Sign in to Messages on this Mac and grant PODA Full Disk Access.')
    try:
        connection = sqlite3.connect('file:' + quote(str(DB_PATH), safe='/') + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('SELECT 1 FROM chat LIMIT 1').fetchone()
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError('PODA cannot read Messages. Grant the PODA app Full Disk Access in macOS Privacy & Security and restart it.') from exc


def chats(limit: int = 100) -> list[dict]:
    with closing(_connect()) as db:
        rows = db.execute('''SELECT c.ROWID, c.guid, c.display_name, c.chat_identifier, c.service_name,
              (SELECT group_concat(h.id, ', ') FROM chat_handle_join ch JOIN handle h ON h.ROWID=ch.handle_id WHERE ch.chat_id=c.ROWID) AS participants,
              (SELECT count(*) FROM chat_handle_join ch WHERE ch.chat_id=c.ROWID) AS participant_count,
              (SELECT max(j.message_id) FROM chat_message_join j WHERE j.chat_id=c.ROWID) AS last_message
              FROM chat c WHERE c.guid IS NOT NULL ORDER BY last_message DESC LIMIT ?''', (max(1, min(limit, 10000)),)).fetchall()
        return [dict(row) for row in rows]


def _decode(text: str | None, blob: bytes | None) -> tuple[str | None, bool]:
    if text is not None:
        return str(text), True
    if blob and len(blob) <= 1024 * 1024:
        decoded = try_parse_attributed_body(blob)
        if decoded is not None:
            return decoded.text, True
    return None, False


def messages(chat_guid: str, *, after: int = 0, limit: int = 50, newest: bool = False) -> list[dict]:
    order = 'DESC' if newest else 'ASC'
    with closing(_connect()) as db:
        found = db.execute(f'''SELECT m.ROWID AS rowid, m.guid, m.text, m.attributedBody, m.date,
             m.is_from_me, m.service, h.id AS sender
             FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID
             JOIN chat c ON c.ROWID=j.chat_id LEFT JOIN handle h ON h.ROWID=m.handle_id
             WHERE c.guid=? AND m.ROWID>? ORDER BY m.ROWID {order} LIMIT ?''',
             (chat_guid, max(0, after), max(1, min(limit, 100)))).fetchall()
    result = []
    for row in found:
        item = dict(row)
        text, readable = _decode(item.pop('text'), item.pop('attributedBody'))
        raw = item.pop('date') or 0
        try:
            seconds = raw / 1e9 if abs(raw) > 10**12 else raw
            date = (datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
        except (OverflowError, ValueError, TypeError):
            date = None
        result.append({**item, 'text': text[:10000] if text is not None else None,
                       'text_available': readable, 'date': date, 'is_from_me': bool(item['is_from_me'])})
    return result


def send(chat_guid: str, text: str) -> None:
    if sys.platform != 'darwin':
        raise RuntimeError('Apple Messages is available on macOS only.')
    if not text or len(text) > 4000:
        raise ValueError('Message must contain 1 to 4000 characters.')
    script = '''on run argv
      tell application "Messages"
        send (item 2 of argv) to chat id (item 1 of argv)
      end tell
    end run'''
    try:
        result = subprocess.run(['/usr/bin/osascript', '-e', script, chat_guid, text], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Messages.app could not send the message.') from exc
    if result.returncode:
        raise RuntimeError('Messages.app did not confirm sending. Check Messages sign-in and macOS Automation permission.')


_CONTACT_CACHE: tuple[float, dict[str, str]] = (0.0, {})


def contact_names() -> dict[str, str]:
    """Ask Contacts.app for names; denial leaves message numbers usable."""
    global _CONTACT_CACHE
    if sys.platform != 'darwin':
        return {}
    if time.monotonic() - _CONTACT_CACHE[0] < 60:
        return _CONTACT_CACHE[1]
    script = r'''const contacts = Application('Contacts');
const out = {};
for (const person of contacts.people()) {
  const name = person.name();
  for (const p of person.phones()) { out[p.value()] = name; }
  for (const e of person.emails()) { out[e.value()] = name; }
}
JSON.stringify(out);'''
    try:
        import json
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script], capture_output=True, text=True, timeout=30)
        names = json.loads(result.stdout) if result.returncode == 0 else {}
        _CONTACT_CACHE = (time.monotonic(), names if isinstance(names, dict) else {})
        return _CONTACT_CACHE[1]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
