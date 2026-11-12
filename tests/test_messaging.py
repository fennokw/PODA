"""Synthetic tests: never open the user's real Messages database or send messages."""
import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from poda_app.connectors.messaging import apple, remote, service
from poda_app.runtime.db import set_setting


def test_apple_reads_selected_chat_and_decodes_typedstream(tmp_path, monkeypatch):
    path = tmp_path / 'chat.db'
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE chat (guid TEXT, display_name TEXT, chat_identifier TEXT, service_name TEXT);
      CREATE TABLE handle (id TEXT); CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
      CREATE TABLE message (guid TEXT, text TEXT, attributedBody BLOB, date INTEGER, is_from_me INTEGER, service TEXT, handle_id INTEGER);
      CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
      INSERT INTO chat VALUES ('iMessage;-;synthetic', 'Test', 'synthetic', 'iMessage');
      INSERT INTO handle VALUES ('person@example.test'); INSERT INTO chat_handle_join VALUES (1,1);''')
    blob = base64.b64decode('BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKyUvcG9kYSBzZWNyZXQgdGhpcyBpcyBhIHN5bnRoZXRpYyB0ZXN0hoQCaUkBJZKEhIQMTlNEaWN0aW9uYXJ5AJSEAWkAhoY=')
    db.execute('INSERT INTO message VALUES (?,?,?,?,?,?,?)', ('m1', None, blob, 790000000000000000, 1, 'iMessage', 1))
    db.execute('INSERT INTO chat_message_join VALUES (1,1)')
    db.commit(); db.close()
    monkeypatch.setattr(apple, 'DB_PATH', path)
    monkeypatch.setattr(apple.sys, 'platform', 'darwin')
    chats = apple.chats()
    assert chats[0]['guid'] == 'iMessage;-;synthetic' and chats[0]['participant_count'] == 1
    messages = apple.messages(chats[0]['guid'])
    assert messages[0]['text'] == '/poda secret this is a synthetic test'
    assert messages[0]['text_available'] and messages[0]['is_from_me']
    assert apple.messages('other-chat') == []


def test_conversation_grants_and_offline_mode(client, monkeypatch):
    set_setting(service._KEY, '{}')
    monkeypatch.setattr(service, 'list_chats', lambda provider: [{'id': 'chat-a'}])
    monkeypatch.setattr(service.apple, 'messages', lambda *a, **kw: [{'text': 'synthetic'}])
    monkeypatch.setattr(service.apple, 'send', lambda *a: None)
    monkeypatch.setattr(service.egress, 'offline_mode', lambda: False)
    try:
        with pytest.raises(PermissionError): service.read('apple', 'chat-a')
        service.set_access('apple', ['chat-a'], read=True, write=False)
        assert service.read('apple', 'chat-a')[0]['text'] == 'synthetic'
        with pytest.raises(PermissionError): service.send('apple', 'chat-a', 'hi')
        with pytest.raises(ValueError): service.set_access('apple', ['chat-b'], read=True, write=True)
        service.set_access('apple', ['chat-a'], read=True, write=True)
        assert service.send('apple', 'chat-a', 'hi')['verified'] is False
        monkeypatch.setattr(service.egress, 'offline_mode', lambda: True)
        with pytest.raises(PermissionError): service.send('apple', 'chat-a', 'hi')
    finally:
        set_setting(service._KEY, '{}')


def test_remote_pairing_scopes_replay_and_revocation(client, monkeypatch):
    from poda_app.connectors.notion import state
    secret = 'abcdefghijklmnopqrstuvwxyz123456'
    keys = {}
    set_setting(service._KEY, json.dumps({'apple': {'chats': ['chat-a'], 'read': True, 'write': True}}))
    set_setting(remote._SETTING, '{}')
    monkeypatch.setattr(remote.keychain, 'keychain_set', lambda service, name, val: keys.update({name: val}))
    monkeypatch.setattr(remote.keychain, 'keychain_get', lambda service, name: keys.get(name))
    monkeypatch.setattr(remote.keychain, 'keychain_delete', lambda service, name: keys.pop(name, None))
    monkeypatch.setattr(service, 'list_chats', lambda provider: [{'id': 'chat-a', 'service': 'iMessage', 'participant_count': 1}])
    monkeypatch.setattr(state, 'capability', lambda: {'can_insert_content': True, 'mapping_active': True, 'data_source_id': 'calendar-a'})
    monkeypatch.setattr(remote.egress, 'offline_mode', lambda: False)
    events = []
    monkeypatch.setattr(apple, 'messages', lambda chat, **kw: ([{'rowid': 100}] if kw.get('newest') else [m for m in events if m['rowid'] > kw.get('after', 0)]))
    sent, answered = [], []
    monkeypatch.setattr(remote, '_answer', lambda command, cfg: (answered.append(command) or 'Verified response'))
    monkeypatch.setattr(service, 'send', lambda provider, chat, text: sent.append((provider, chat, text)))
    try:
        with pytest.raises(ValueError): remote.configure(True, 'chat-a', secret, True, False)
        assert remote.configure(True, 'chat-a', secret, True, True)['enabled']
        cfg = remote.config()
        assert remote._authorized(cfg, 'Add a meeting tomorrow at 5pm to my calendar', 'notion_calendar_create', {})[0]
        assert not remote._authorized(cfg, 'Add something to my calendar', 'notion_calendar_create', {})[0]
        assert not remote._authorized(cfg, 'Add a meeting tomorrow at 5pm', 'mail_send', {})[0]
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        def msg(rowid, text, mine=True, when=now, protocol='iMessage'):
            return {'rowid': rowid, 'text': text, 'text_available': True, 'is_from_me': mine, 'date': when.isoformat(), 'service': protocol}
        events.extend([msg(101, '/poda badkey dangerous'), msg(102, f'/poda {secret} injected', False),
                       msg(103, f'/poda {secret} old', when=now-timedelta(minutes=11)),
                       msg(104, f'/poda {secret} valid'), msg(105, f'/poda {secret} sms', protocol='SMS')])
        assert remote.poll_once() == 1
        assert answered == ['valid'] and sent == [('apple', 'chat-a', '[PODA]: Verified response')]
        assert remote.poll_once() == 0
        remote.configure(False)
        assert not remote._authorized(cfg, 'Add a meeting tomorrow at 5pm', 'notion_calendar_create', {})[0]
        assert remote.poll_once() == 0
    finally:
        set_setting(service._KEY, '{}'); set_setting(remote._SETTING, '{}')


def test_remote_planner_rejects_hidden_tool_calls(monkeypatch):
    from poda_app.agent import orchestrator
    from poda_app.runtime import inference
    calls = iter([{'message': {'tool_calls': [{'function': {'name': 'fs_write', 'arguments': {'path': '/tmp/no', 'content': 'bad'}}}]}},
                  {'message': {'content': 'done'}}])
    monkeypatch.setattr(orchestrator, '_planner_models', lambda *args: ['test'])
    monkeypatch.setattr(inference, 'chat', lambda *args, **kwargs: next(calls))
    monkeypatch.setattr(orchestrator.tools, 'model_tool_specs', lambda *args: [{'function': {'name': 'notion_agenda'}}])
    monkeypatch.setattr(orchestrator.tools, 'execute', lambda *args, **kwargs: pytest.fail('unauthorized tool ran'))
    result = orchestrator.plan('hello', {}, unattended=True, allowed_tools={'notion_agenda'}, unattended_authorizer=lambda *_: (True, 'yes'))
    assert any(x['name'] == 'fs_write' and 'outside' in x['reason'] for x in result['flagged'])


def test_groupme_uses_allowlisted_provider_and_scoped_groups(client, monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return {'response': self.body}
    requests = []
    def guarded(service_name, method, url, **kwargs):
        requests.append((service_name, method, url, kwargs))
        assert kwargs['headers']['X-Access-Token'] == 'synthetic-token'
        if url.endswith('groups?per_page=100&omit=memberships'):
            return Response([{'id': '42', 'name': 'Demo group'}])
        if method == 'GET': return Response({'messages': [{'id': '1', 'name': 'A', 'text': 'hello'}]})
        return Response({'message': {'id': '2'}})
    monkeypatch.setattr(service.keychain, 'keychain_get', lambda *args: 'synthetic-token')
    monkeypatch.setattr(service.egress, 'guarded_request', guarded)
    monkeypatch.setattr(service.egress, 'offline_mode', lambda: False)
    set_setting(service._KEY, '{}')
    try:
        service.set_access('groupme', ['42'], read=True, write=True)
        assert service.read('groupme', '42')[0]['text'] == 'hello'
        assert service.send('groupme', '42', 'test')['verified']
        with pytest.raises(PermissionError): service.read('groupme', '43')
        assert all(item[0] == 'groupme' and item[2].startswith('https://api.groupme.com/') for item in requests)
        assert all('synthetic-token' not in item[2] for item in requests)
    finally:
        set_setting(service._KEY, '{}')


def test_groupme_remote_accepts_only_connected_account_once(client, monkeypatch):
    secret = 'abcdefghijklmnopqrstuvwxyz123456'
    keys = {}
    set_setting(service._KEY, json.dumps({'groupme': {'chats': ['42'], 'read': True, 'write': True}}))
    set_setting(remote._SETTING, '{}')
    monkeypatch.setattr(remote.keychain, 'keychain_set', lambda service, name, val: keys.update({name: val}))
    monkeypatch.setattr(remote.keychain, 'keychain_get', lambda service, name: keys.get(name))
    monkeypatch.setattr(remote.keychain, 'keychain_delete', lambda service, name: keys.pop(name, None))
    monkeypatch.setattr(service, 'list_chats', lambda provider: [{'id': '42', 'service': 'GroupMe'}])
    monkeypatch.setattr(service, 'read', lambda *a, **k: [{'id': '100'}])
    monkeypatch.setattr(remote.egress, 'offline_mode', lambda: False)
    now = int(datetime.now(timezone.utc).timestamp())
    messages = [
        {'id': '101', 'user_id': '999', 'sender_type': 'user', 'created_at': now, 'text': f'/poda {secret} intruder'},
        {'id': '102', 'user_id': '17', 'sender_type': 'bot', 'created_at': now, 'text': f'/poda {secret} bot'},
        {'id': '103', 'user_id': '17', 'sender_type': 'user', 'created_at': now, 'text': f'/poda {secret} legitimate'},
    ]
    def request(method, path, payload=None):
        if path == 'users/me': return {'id': '17'}
        assert path == 'groups/42/messages?limit=50&after_id=100'
        return {'messages': messages}
    monkeypatch.setattr(service, '_groupme_request', request)
    answered, sent = [], []
    monkeypatch.setattr(remote, '_answer', lambda command, cfg: (answered.append(command) or 'Done'))
    monkeypatch.setattr(service, 'send', lambda provider, chat, text: sent.append((provider, chat, text)))
    try:
        assert remote.configure(True, '42', secret, False, True, 'groupme')['enabled']
        assert remote.poll_once() == 1
        assert answered == ['legitimate'] and sent == [('groupme', '42', '[PODA]: Done')]
        assert remote.config()['cursor'] == '103'
    finally:
        set_setting(service._KEY, '{}'); set_setting(remote._SETTING, '{}')


def test_notification_reminders_and_coding_are_scoped_and_deduplicated(client, monkeypatch):
    from poda_app.connectors.messaging import notifications
    from poda_app.connectors.notion import mapping
    set_setting(service._KEY, json.dumps({'apple': {'chats': ['chat-a'], 'write': True}}))
    set_setting(notifications._SENT, '{}')
    monkeypatch.setattr(notifications.egress, 'offline_mode', lambda: False)
    sent = []
    monkeypatch.setattr(service, 'send', lambda provider, chat, text: sent.append((provider, chat, text)))
    now = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
    events = [{'page_id': 'event1', 'title': 'Team meeting', 'start_local': (now + timedelta(minutes=10)).isoformat(), 'all_day': False, 'is_deadline': False},
              {'page_id': 'allday', 'title': 'All day', 'start_local': (now + timedelta(minutes=10)).isoformat(), 'all_day': True}]
    monkeypatch.setattr(mapping, 'items_between_effective', lambda *a, **kw: {'items': events})
    try:
        with pytest.raises(ValueError): notifications.configure(True, 'apple', 'other', True, True, 10, 'UTC')
        notifications.configure(True, 'apple', 'chat-a', True, True, 10, 'UTC')
        assert notifications.reminder_tick(now) == 1
        assert 'Team meeting' in sent[0][2]
        assert notifications.reminder_tick(now + timedelta(seconds=20)) == 0
        notifications.coding_finished()
        assert 'finished generating' in sent[1][2]
        set_setting(service._KEY, '{}')
        notifications.coding_finished()
        assert len(sent) == 2
    finally:
        set_setting(service._KEY, '{}'); set_setting(notifications._KEY, '{}'); set_setting(notifications._SENT, '{}')


def test_command_key_is_redacted_from_agent_reads(client, monkeypatch):
    set_setting(service._KEY, json.dumps({'apple': {'chats': ['chat-a'], 'read': True}}))
    monkeypatch.setattr(apple, 'messages', lambda *a, **kw: [{'text': '/poda secret-key create a meeting'}])
    monkeypatch.setattr(remote, 'config', lambda: {'enabled': True})
    monkeypatch.setattr(remote.keychain, 'keychain_get', lambda *args: 'secret-key')
    try:
        assert service.read('apple', 'chat-a')[0]['text'] == '/poda [private key] create a meeting'
    finally:
        set_setting(service._KEY, '{}')


def test_daily_message_scope_revocation_and_final_delivery(client, monkeypatch):
    from poda_app.daily_updates import permissions, service as daily
    from poda_app.runtime.db import connect, one
    set_setting(service._KEY, json.dumps({'apple': {'chats': ['chat-a'], 'write': True}}))
    monkeypatch.setattr(service, 'list_chats', lambda provider: [{'id': 'chat-a', 'name': 'Private'}])
    sent = []
    monkeypatch.setattr(service, 'send', lambda p, c, t: (sent.append((p, c, t)) or {'submitted': True, 'verified': False}))
    grant = {'tool': 'messages_send', 'scope': {'provider': 'apple', 'conversation_id': 'chat-a'}}
    payload = {'title': 'Morning update', 'prompt': 'Summarize the day', 'times': ['08:00'], 'timezone': 'UTC', 'permissions': [grant]}
    created = client.post('/daily-updates', json=payload)
    assert created.status_code == 200, created.text
    task = created.json()['schedule']
    run_id = 'synthetic-delivery'
    db = connect()
    daily.ensure_tables(db)
    db.execute("INSERT INTO daily_prompt_runs (id,prompt_id,slot_key,scheduled_for,status) VALUES (?,?,?,?,?)", (run_id, task['id'], 'manual:synthetic', '2026-11-12', 'running'))
    db.commit(); db.close()
    try:
        args = {'provider': 'apple', 'conversation_id': 'chat-a', 'text': 'hello'}
        assert permissions.authorize(task['id'], task['updated_at'], 'messages_send', args)[0]
        assert not permissions.authorize(task['id'], task['updated_at'], 'messages_send', {**args, 'conversation_id': 'other'})[0]
        assert not permissions.authorize(task['id'], 'old-revision', 'messages_send', args)[0]
        daily._deliver_update(run_id, task, 'A verified completed result.')
        assert len(sent) == 1 and 'A verified completed result.' in sent[0][2]
        db=connect(); row=one(db, 'SELECT * FROM daily_prompt_runs WHERE id=?', (run_id,)); db.close()
        assert row['message_delivery_status'] == 'submitted'
        set_setting(service._KEY, '{}')
        daily._deliver_update(run_id, task, 'Do not send')
        assert len(sent) == 1
        db=connect(); row=one(db, 'SELECT * FROM daily_prompt_runs WHERE id=?', (run_id,)); db.close()
        assert row['message_delivery_status'] == 'failed'
    finally:
        client.delete('/daily-updates/'+task['id'])
        set_setting(service._KEY, '{}')


def test_daily_delivery_caps_messages_and_reports_truncation(client, monkeypatch):
    from poda_app.daily_updates import permissions, service as daily
    set_setting(service._KEY, json.dumps({'groupme': {'chats': ['42'], 'write': True}}))
    monkeypatch.setattr(permissions, 'authorize', lambda *a: (True, 'approved'))
    sent=[]
    statuses=[]
    monkeypatch.setattr(daily, '_message_delivery_status', lambda *args: statuses.append(args))
    monkeypatch.setattr(service, 'send', lambda p,c,t: (sent.append(t) or {'verified': True}))
    task={'id':'daily','updated_at':'revision','title':'Long update','permissions_json':json.dumps([{'tool':'messages_send','scope':{'provider':'groupme','conversation_id':'42'}}])}
    try:
        daily._deliver_update('run', task, 'long content ' * 1000)
        assert len(sent) == 5 and all(len(s) <= 1000 for s in sent)
        assert 'shortened' in sent[-1] and statuses[-1][1] == 'sent'
    finally:
        set_setting(service._KEY, '{}')


def test_daily_only_delivers_after_completed_stream(client, monkeypatch):
    import asyncio
    from poda_app.daily_updates import service as daily
    from poda_app.chat import router as chat_router
    from poda_app.runtime.db import connect
    output = []
    monkeypatch.setattr(daily, '_deliver_update', lambda run, task, text: output.append(text))
    monkeypatch.setattr(daily, '_notify', lambda *args: False)
    frames = []
    async def fake_run(*args, **kwargs):
        class Response:
            async def body_iterator(self):
                for frame in frames:
                    yield frame
        result=Response()
        result.body_iterator=result.body_iterator()
        return result
    monkeypatch.setattr(chat_router, 'run_chat', fake_run)
    task={'id':'synthetic-task','title':'Test','prompt':'test','timezone':'UTC','updated_at':'rev','mail_account_ids_json':'[]'}
    db=connect();daily.ensure_tables(db)
    for run in ('finished', 'interrupted'):
        db.execute("INSERT INTO daily_prompt_runs (id,prompt_id,slot_key,scheduled_for,status) VALUES (?,?,?,?,?)",(run,task['id'],run,'2026-11-12','queued'))
    db.commit();db.close()
    frames.extend(['event: token\ndata: {"token":"final result"}\n\n', 'event: done\ndata: {}\n\n'])
    asyncio.run(daily.execute('finished',task))
    assert output == ['final result']
    frames.pop()
    asyncio.run(daily.execute('interrupted',task))
    assert output == ['final result']
    assert next(r for r in daily.history(300) if r['id']=='interrupted')['status'] == 'failed'


def test_all_chat_grants_and_contact_search(client, monkeypatch):
    from poda_app.connectors.messaging import tools as message_tools
    monkeypatch.setattr(apple, 'contact_names', lambda: {'+1 (617) 555-0100': 'Alex Example'})
    monkeypatch.setattr(apple, 'chats', lambda **kw: [{'guid':'iMessage;-;6175550100', 'participants':'+16175550100', 'service_name':'iMessage'}])
    try:
        service.set_access('apple', [], read=True, write=True, all_chats=True)
        assert service.allowed('apple', 'any-chat', 'read') and message_tools._available('write')
        assert service.list_chats('apple', query='Alex')[0]['name'] == 'Alex Example'
        assert service.list_chats('apple', query='617')[0]['name'] == 'Alex Example'
        service.set_access('apple', [], read=False, write=False)
        assert not service.allowed('apple', 'any-chat', 'write')
    finally:
        set_setting(service._KEY, '{}')


def test_monitored_chat_prefix_senders_cursor_and_reply(client, monkeypatch):
    from poda_app.connectors.messaging import chat_commands as commands
    set_setting(service._KEY, json.dumps({'apple': {'chats':['chat-a'], 'read':True, 'write':True}}))
    set_setting(commands._KEY, '{}');set_setting(remote._SETTING, '{}')
    monkeypatch.setattr(service, 'list_chats', lambda p: [{'id':'chat-a','service':'iMessage'}])
    monkeypatch.setattr(commands.egress, 'offline_mode', lambda: False)
    events=[]
    monkeypatch.setattr(apple, 'messages', lambda chat, **kw: ([{'rowid':100}] if kw.get('newest') else [x for x in events if x['rowid'] > kw.get('after', 0)]))
    answered=[];sent=[]
    monkeypatch.setattr(commands, 'answer', lambda command,cfg: (answered.append(command) or 'Done'))
    monkeypatch.setattr(service, 'send', lambda p,c,t: sent.append((p,c,t)))
    try:
        commands.save('apple','chat-a',True,prefix='/HELP',senders=['self','+1 (617) 555-0100'],confirm=True)
        now=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        def event(i,text,sender=None,mine=False):
            return {'rowid':i,'text':text,'sender':sender,'is_from_me':mine,'text_available':True,'date':now}
        events.extend([event(101,'hello',mine=True),event(102,'/HELP bad','+16175550101'),event(103,'/HELPful wrong',mine=True),
                       event(104,'/help question','6175550100'),event(105,'/HELP mine',mine=True),event(106,'[PODA]: reply',mine=True)])
        assert commands.poll_once()==2
        assert answered==['question','mine']
        assert all(text.startswith('[PODA]:') for _,_,text in sent)
        assert commands.poll_once()==0
        commands.save('apple','chat-a',False)
        assert commands.poll_once()==0
    finally:
        set_setting(commands._KEY, '{}');set_setting(service._KEY, '{}')


def test_remote_code_scope_blocks_other_folders(tmp_path, client, monkeypatch):
    from poda_app.connectors.messaging import chat_commands as commands
    from poda_app.agent import grants
    project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(commands, '_live', lambda cfg: True)
    monkeypatch.setattr(grants, 'resolve', lambda path, mode: (path,{}))
    cfg={'coding_root':str(project)}
    assert commands._guard(cfg,'fs_read',{'path':str(project/'main.py')})[0]
    assert not commands._guard(cfg,'fs_read',{'path':str(tmp_path/'outside.py')})[0]
    assert not commands._guard({'coding_root':''},'fs_edit',{'path':str(project/'main.py')})[0]


def test_send_as_user_has_poda_prefix(monkeypatch):
    from poda_app.connectors.messaging import tools as msgtools
    sent=[]
    monkeypatch.setattr(service, 'send', lambda p,c,t: (sent.append(t) or {'submitted':True}))
    assert msgtools.send_message('apple','chat-a','Hello')['ok']
    assert msgtools.send_message('apple','chat-a','[PODA]: Already marked')['ok']
    assert sent == ['[PODA]: Hello','[PODA]: Already marked']


def test_chat_monitor_multiple_conversations_and_live_revoke(client, monkeypatch):
    from poda_app.connectors.messaging import chat_commands as commands
    set_setting(service._KEY,json.dumps({'apple':{'chats':['a','b'],'read':True,'write':True}}))
    set_setting(commands._KEY,'{}');set_setting(remote._SETTING,'{}')
    monkeypatch.setattr(service,'list_chats',lambda p:[{'id':'a'},{'id':'b'}])
    monkeypatch.setattr(apple,'messages',lambda *a,**kw:[])
    monkeypatch.setattr(commands.egress,'offline_mode',lambda:False)
    try:
        a=commands.save('apple','a',True,confirm=True)
        b=commands.save('apple','b',True,prefix='/ASK',confirm=True)
        assert len(commands.config()) == 2 and commands._live(a) and commands._live(b)
        commands.save('apple','a',False)
        assert not commands._live(a) and commands._live(b)
        set_setting(service._KEY,'{}')
        assert not commands._live(b)
    finally:
        set_setting(commands._KEY,'{}');set_setting(service._KEY,'{}')
