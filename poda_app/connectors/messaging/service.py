"""Explicit conversation-scoped messaging access, kept local except for provider APIs."""
from __future__ import annotations

import json
import re
from typing import Any

from ...runtime.db import get_setting, set_setting
from ...security import egress, keychain
from . import apple

_KEY = 'messaging_access_v1'
_GROUPME_SERVICE = 'messaging-groupme'


def config() -> dict:
    try:
        value = json.loads(get_setting(_KEY, '') or '{}')
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def set_access(provider: str, chats: list[str], *, read: bool, write: bool) -> dict:
    if provider not in {'apple', 'groupme'} or len(chats) > 50 or any(not isinstance(x, str) or not x or len(x) > 300 for x in chats):
        raise ValueError('Choose up to 50 valid conversations.')
    available = {row['id'] for row in list_chats(provider)} if read or write else set(chats)
    if not set(chats).issubset(available):
        raise ValueError('Choose conversations from the current provider list.')
    data = config()
    data[provider] = {'chats': sorted(set(chats)), 'read': bool(read), 'write': bool(write)}
    set_setting(_KEY, json.dumps(data))
    return data[provider]


def _groupme_request(method: str, path: str, payload: dict | None = None) -> Any:
    token = keychain.keychain_get(_GROUPME_SERVICE, 'token')
    if not token:
        raise RuntimeError('Connect GroupMe first.')
    response = egress.guarded_request('groupme', method, 'https://api.groupme.com/v3/' + path,
                                      headers={'X-Access-Token': token}, json_body=payload,
                                      purpose='GroupMe messaging')
    if response.status_code == 304:
        return {}
    if response.status_code >= 400:
        raise RuntimeError(f'GroupMe rejected the request (HTTP {response.status_code}).')
    return response.json().get('response') or {}


def list_chats(provider: str) -> list[dict]:
    if provider == 'apple':
        return [{'id': row['guid'], 'name': row.get('display_name') or row.get('participants') or row.get('chat_identifier') or 'Conversation',
                 'service': row.get('service_name'), 'participants': row.get('participants') or '',
                 'participant_count': row.get('participant_count', 0)} for row in apple.chats()]
    if provider == 'groupme':
        groups = _groupme_request('GET', 'groups?per_page=100&omit=memberships')
        return [{'id': str(row['id']), 'name': str(row.get('name') or 'Group')[:120], 'service': 'GroupMe'}
                for row in groups if re.fullmatch(r'[0-9]+', str(row.get('id') or ''))]
    raise ValueError('Unsupported messaging provider.')


def allowed(provider: str, chat_id: str, action: str) -> bool:
    grant = config().get(provider) or {}
    return bool(grant.get(action) and chat_id in (grant.get('chats') or []))


def _redact_key(items: list[dict]) -> list[dict]:
    for item in items:
        text = item.get('text') or ''
        parts = text.split(None, 2)
        if len(parts) >= 2 and parts[0].lower() == '/poda':
            item['text'] = '/poda [private key]' + (' ' + parts[2] if len(parts) == 3 else '')
    return items


def read(provider: str, chat_id: str, limit: int = 30) -> list[dict]:
    if not allowed(provider, chat_id, 'read'):
        raise PermissionError('Reading this conversation is not enabled in Messages settings.')
    if provider == 'apple':
        return _redact_key(apple.messages(chat_id, newest=True, limit=limit))
    if provider == 'groupme' and re.fullmatch(r'[0-9]+', chat_id):
        data = _groupme_request('GET', f'groups/{chat_id}/messages?limit={max(1, min(limit, 100))}')
        return _redact_key([{'id': str(m.get('id') or ''), 'sender': str(m.get('name') or ''), 'text': str(m.get('text') or '')[:10000],
                 'date': m.get('created_at'), 'sender_id': str(m.get('user_id') or ''), 'sender_type': str(m.get('sender_type') or '')} for m in data.get('messages', [])])
    raise ValueError('Invalid provider or conversation.')


def send(provider: str, chat_id: str, text: str) -> dict:
    if egress.offline_mode():
        raise PermissionError('PODA is in Offline Mode; message sending is disabled.')
    if not allowed(provider, chat_id, 'write'):
        raise PermissionError('Sending to this conversation is not enabled in Messages settings.')
    if provider == 'apple':
        apple.send(chat_id, text)
        return {'submitted': True, 'verified': False, 'note': 'Messages.app accepted the send; delivery was not independently verified.'}
    if provider == 'groupme' and re.fullmatch(r'[0-9]+', chat_id):
        import uuid
        if not text or len(text) > 1000:
            raise ValueError('GroupMe messages must contain 1 to 1000 characters.')
        response = _groupme_request('POST', f'groups/{chat_id}/messages', {'message': {'source_guid': str(uuid.uuid4()), 'text': text}})
        message = response.get('message') or {}
        if not message.get('id'):
            raise RuntimeError('GroupMe accepted the request but did not return a message ID; check the conversation before retrying.')
        return {'submitted': True, 'verified': True, 'message_id': str(message['id'])}
    raise ValueError('Invalid provider or conversation.')
