"""Local UI endpoints for explicitly scoped messaging connections."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...security import keychain
from . import remote, service, notifications

router = APIRouter(prefix='/messaging', tags=['messaging'])


class Access(BaseModel):
    chats: list[str] = Field(default_factory=list)
    read: bool = False
    write: bool = False


class Token(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class Remote(BaseModel):
    enabled: bool
    provider: str = 'apple'
    chat_id: str = ''
    secret: str = ''
    calendar_create: bool = False
    confirm: bool = False


class Notifications(BaseModel):
    enabled: bool = False
    provider: str = 'apple'
    chat_id: str = ''
    coding: bool = False
    reminders: bool = False
    minutes: int = 10
    timezone: str = 'America/New_York'


@router.put('/notifications/config')
def notification_config(body: Notifications):
    try:
        return notifications.configure(body.enabled, body.provider, body.chat_id, body.coding, body.reminders, body.minutes, body.timezone)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/status')
def status():
    saved = service.config()
    remote_config = remote.config()
    return {'access': saved, 'notifications': notifications.config(), 'groupme_connected': keychain.keychain_exists(service._GROUPME_SERVICE, 'token'),
            'remote': {'enabled': bool(remote_config.get('enabled')), 'provider': remote_config.get('provider', 'apple'), 'chat_id': remote_config.get('chat_id'),
                       'calendar_create': bool(remote_config.get('calendar_create'))},
            'supported': {'apple': 'Mac Messages (local database and Messages.app)', 'groupme': 'GroupMe group API',
                          'whatsapp': 'Not connected: official API requires a WhatsApp Business account and hosted webhooks.',
                          'instagram': 'Not connected: official API requires a professional account and hosted webhooks.'}}


@router.get('/{provider}/conversations')
def conversations(provider: str):
    try:
        return {'conversations': service.list_chats(provider)}
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put('/{provider}/access')
def access(provider: str, body: Access):
    try:
        return service.set_access(provider, body.chats, read=body.read, write=body.write)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/groupme/connect')
def groupme_connect(body: Token):
    from ...security import egress
    try:
        response = egress.guarded_request('groupme', 'GET', 'https://api.groupme.com/v3/groups?per_page=1',
                                          headers={'X-Access-Token': body.token}, purpose='Verify GroupMe connection')
        if response.status_code >= 400:
            raise RuntimeError('Provider rejected token')
        response.json()
    except Exception as exc:
        raise HTTPException(409, 'GroupMe connection could not be verified; existing credentials were not changed.') from exc
    keychain.keychain_set(service._GROUPME_SERVICE, 'token', body.token)
    return {'connected': True}


@router.delete('/groupme/connect')
def groupme_disconnect():
    service.set_access('groupme', [], read=False, write=False)
    keychain.keychain_delete(service._GROUPME_SERVICE, 'token')
    return {'connected': False}


@router.put('/remote/config')
def remote_config(body: Remote):
    try:
        return remote.configure(body.enabled, body.chat_id, body.secret, body.calendar_create, body.confirm, body.provider)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
