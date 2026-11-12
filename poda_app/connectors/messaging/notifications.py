"""Opt-in, conversation-scoped text notifications."""
from __future__ import annotations
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ...runtime.db import get_setting, set_setting
from ...security import egress
from . import service

_KEY = 'messaging_notifications_v1'
_SENT = 'messaging_notification_sent_v1'


def config():
    try:
        data = json.loads(get_setting(_KEY, '') or '{}')
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def configure(enabled, provider, chat_id, coding, reminders, minutes, timezone_name):
    if enabled:
        if provider not in {'apple', 'groupme'} or not service.allowed(provider, chat_id, 'write'):
            raise ValueError('First enable sending for the selected conversation.')
        if not 1 <= minutes <= 180:
            raise ValueError('Choose a reminder between 1 and 180 minutes before the event.')
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError('Choose a valid IANA time zone.') from exc
    data = {'enabled': bool(enabled), 'provider': provider, 'chat_id': chat_id,
            'coding': bool(coding), 'reminders': bool(reminders), 'minutes': minutes, 'timezone': timezone_name}
    set_setting(_KEY, json.dumps(data))
    return data


def _send(cfg, message):
    if not cfg.get('enabled') or egress.offline_mode() or not service.allowed(cfg.get('provider', ''), cfg.get('chat_id', ''), 'write'):
        return False
    # Recheck the live settings in case the user disabled notifications while work was running.
    if config() != cfg:
        return False
    service.send(cfg['provider'], cfg['chat_id'], ('[PODA]: ' + message)[:1000])
    return True


def coding_finished():
    try:
        cfg = config()
        if cfg.get('coding'):
            _send(cfg, 'PODA finished generating your coding response. Open PODA to review the result and any verification details.')
    except Exception:
        # Never interrupt the chat response if notifications are unavailable.
        pass


def reminder_tick(now=None):
    cfg = config()
    if not cfg.get('enabled') or not cfg.get('reminders') or egress.offline_mode() or not service.allowed(cfg.get('provider', ''), cfg.get('chat_id', ''), 'write'):
        return 0
    from ..notion import mapping
    zone = ZoneInfo(cfg['timezone'])
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    until = now + timedelta(minutes=cfg['minutes'])
    result = mapping.items_between_effective(now.date().isoformat(), until.date().isoformat(), tz_name=cfg['timezone'])
    try:
        sent = json.loads(get_setting(_SENT, '') or '{}')
        if not isinstance(sent, dict): sent = {}
    except ValueError:
        sent = {}
    sent = {k: v for k, v in sent.items() if isinstance(v, (int, float)) and v > now.timestamp() - 172800}
    count = 0
    for event in result.get('items', []):
        if event.get('all_day') or event.get('is_deadline') or not event.get('start_local') or not event.get('page_id'):
            continue
        try:
            start = datetime.fromisoformat(event['start_local'])
            if start.tzinfo is None: continue
        except ValueError:
            continue
        remaining = (start - now).total_seconds()
        # A small polling window avoids delayed/stale reminders after downtime.
        if not max(0, cfg['minutes'] * 60 - 90) <= remaining <= cfg['minutes'] * 60:
            continue
        marker = hashlib.sha256((str(event['page_id']) + '|' + start.isoformat() + '|' + str(cfg['minutes'])).encode()).hexdigest()
        if marker in sent: continue
        # Claim before sending: avoids duplicates on uncertain delivery or restart.
        sent[marker] = now.timestamp()
        set_setting(_SENT, json.dumps(sent))
        title = str(event.get('title') or 'Event')[:180]
        if _send(cfg, f"Reminder: {title} starts at {start.strftime('%I:%M %p').lstrip('0')} ({cfg['timezone']})."):
            count += 1
    return count


async def scheduler():
    while True:
        try:
            await asyncio.to_thread(reminder_tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f'[PODA message reminders] {type(exc).__name__}: check calendar and messaging setup.')
        await asyncio.sleep(60)
