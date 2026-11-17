"""Provider/default regressions with isolated storage and mocked Google transport."""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from poda_app.connectors.calendar import google, service
from poda_app.connectors.notion import event_defaults
from poda_app.runtime.db import init_database, get_setting, set_setting


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    init_database()
    old = get_setting('calendar_provider_v1', '')
    vault = {}
    monkeypatch.setattr(google.keychain, 'keychain_get', lambda s, a: vault.get((s, a)))
    monkeypatch.setattr(google.keychain, 'keychain_set', lambda s, a, v: vault.__setitem__((s, a), v))
    monkeypatch.setattr(google.keychain, 'keychain_delete', lambda s, a: vault.pop((s, a), None))
    monkeypatch.setattr(google.egress, 'guarded_request', lambda *a, **k: pytest.fail('Unexpected network request'))
    service.activate('local')
    yield vault
    google.disconnect()
    set_setting('calendar_provider_v1', old)


def test_local_crud_and_defaults(client):
    title = 'Provider round trip'
    created = service.create_calendar_event(title, '2027-02-05T14:00:00-05:00')
    assert created['ok']
    ev = created['result']
    assert ev['end'] == '2027-02-05T15:00:00-05:00'
    assert ev['area'] == ['Personal'] and ev['commitment'] == 'Maybe'
    assert 'Would you like to change' in ev['review_question']
    duplicate = service.create_calendar_event(title, '2027-02-05T19:00:00Z')
    assert duplicate['result']['duplicate']
    assert service.update_calendar_event(ev['page_id'], commitment='Yes', area='Music')['ok']
    agenda = service.items_between_effective('2027-02-05', '2027-02-05')['items']
    saved = next(e for e in agenda if e['page_id'] == ev['page_id'])
    assert saved['start_time'] == '14:00' and saved['areas'] == ['Music']
    assert service.archive_calendar_event(ev['page_id'])['ok']
    assert not any(e['page_id'] == ev['page_id'] for e in service.events_between('2027-02-05','2027-02-05')['events'])


def test_all_day_and_invalid_times():
    assert service.validate('Day', '2027-02-05') == '2027-02-05'
    for start, end in [('2027-02-05T14:00:00',''), ('2027-02-05T14:00:00Z','2027-02-05T13:00:00Z')]:
        assert not service.create_calendar_event('Invalid',start,end)['ok']
    with pytest.raises(ValueError): service.events_between('2027-01-01','2028-01-01')


def test_readonly_and_destination_binding():
    local = service.binding()
    assert not service.matches({'data_source_id':'local'})  # old grants mean Notion
    service.activate('local', allow_write=False)
    assert not service.create_calendar_event('Blocked', '2027-02-05')['ok']
    service.activate('notion')
    with service.bound(local):
        assert not service.create_calendar_event('Stale prompt', '2027-02-05')['ok']


def test_update_uses_update_permission(monkeypatch):
    from poda_app.connectors.notion import automation
    monkeypatch.setattr(service, 'capability', lambda: {'provider':'notion','can_read_content':True,'can_insert_content':False,'can_update_content':True})
    monkeypatch.setattr(automation, 'update_calendar_event', lambda *a: {'ok':True})
    assert service.update_calendar_event('id', title='Updated')['ok']
    assert not service.create_calendar_event('Blocked','2027-02-05')['ok']


def schema():
    return {k:{'type':kind, kind:{'options':[{'name':n} for n in names]}} for k,kind,names in [
        ('Type','select',['Homework','Event']),('Commitment','select',['No','Maybe','Yes']),
        ('Area','multi_select',['Music','Personal']),('Source Status','status',['Done','Active'])]}


def test_notion_defaults_use_schema_and_event_type():
    props, provisional, missing = event_defaults.resolve(schema(), {'default_type':'Homework'}, 'defaults-test')
    assert props['Type']['select']['name'] == 'Event'
    assert props['Source Status']['status']['name'] == 'Active'
    assert provisional == {'commitment':'Maybe','area':'Personal'} and not missing
    explicit, provisional, _ = event_defaults.resolve(schema(), {}, 'defaults-test', {'commitment':'Yes','area':'Music'})
    assert explicit['Area']['multi_select'] == [{'name':'Music'}] and not provisional
    with pytest.raises(ValueError): event_defaults.resolve(schema(), {}, 'defaults-test', {'area':'Invented'})


def test_defaults_scoped_and_relation_not_fabricated():
    event_defaults.save('one-source',schema(),{}, {'area':'Music'})
    assert event_defaults.resolve(schema(),{},'one-source')[1]['area']=='Music'
    assert event_defaults.resolve(schema(),{},'other-source')[1]['area']=='Personal'
    props, _, missing = event_defaults.resolve({'Area':{'type':'relation'}},{},'relations')
    assert not props and missing


def test_google_activation_checks_read_access(monkeypatch):
    monkeypatch.setattr(google,'calendars',lambda:[{'id':'read','summary':'Read','accessRole':'reader'}])
    monkeypatch.setattr(google,'connected',lambda:True)
    with pytest.raises(ValueError): service.activate('google','read')
    assert service.config()['provider']=='local'
    assert not service.activate('google','read',False)['can_insert_content']


def test_google_all_day_write_verification_and_pagination(monkeypatch):
    set_setting('calendar_provider_v1',json.dumps({'provider':'google','calendar_id':'a@example.com','allow_write':True}))
    monkeypatch.setattr(google,'connected',lambda:True)
    saved = {}; calls = []
    def request(method,path,body=None):
        calls.append((method,path,body))
        if method=='POST': saved.update(body);return saved
        if '/events?' in path: return {'items':[]}
        return saved
    monkeypatch.setattr(google,'_request',request)
    out=service.create_calendar_event('Day','2027-02-05')
    assert out['ok'] and out['result']['end']=='2027-02-05'
    assert saved['end']=={'date':'2027-02-06'}
    assert calls[1][1].startswith('calendars/a%40example.com/')
    pages=iter([{'items':[1],'nextPageToken':'next token'},{'items':[2]}])
    monkeypatch.setattr(google,'_request',lambda *a: next(pages))
    assert google._pages('events?maxResults=1')==[1,2]


def test_google_verification_mismatch_is_not_success(monkeypatch):
    monkeypatch.setattr(google,'_path',lambda ident: ident)
    monkeypatch.setattr(google,'_request',lambda *a: {'id':'x','summary':'wrong','start':{'dateTime':'2027-02-05T14:00:00Z'},'end':{'dateTime':'2027-02-05T15:00:00Z'}})
    assert not google._verify('x',{'summary':'expected'},{'created':True})['ok']


def test_google_refresh_keeps_secrets_out_of_status(monkeypatch):
    google._save('tokens',{'refresh_token':'refresh-secret','expires_at':0})
    google._save('client',{'client_id':'id','client_secret':'client-secret'})
    monkeypatch.setattr(google,'_token',lambda data: {'access_token':'access-secret','expires_in':3600})
    class Response:
        ok=True;content=b'{}'
        def json(self): return {}
    calls=[]
    monkeypatch.setattr(google.egress,'guarded_request',lambda *a,**k: (calls.append(k) or Response()))
    google._request('GET','users/me/calendarList')
    assert google._get('tokens')['refresh_token']=='refresh-secret'
    assert calls[0]['headers']['Authorization']=='Bearer access-secret'
    assert 'secret' not in json.dumps(google.status())


def test_oauth_pkce_state_and_cancel_without_socket(monkeypatch):
    google.configure({'installed':{'client_id':'example.apps.googleusercontent.com','client_secret':'client-secret'}})
    monkeypatch.setattr(google.egress,'offline_mode',lambda:False)
    class Server:
        server_port=12345
        def __init__(self,*args): pass
    class Thread:
        def __init__(self,**kw): pass
        def start(self): pass
    monkeypatch.setattr(google,'HTTPServer',Server)
    monkeypatch.setattr(google.threading,'Thread',Thread)
    q=parse_qs(urlparse(google.start_oauth()['url']).query)
    assert q['code_challenge_method']==['S256'] and len(q['state'][0])>30
    assert q['redirect_uri']==['http://127.0.0.1:12345/callback']
    assert 'client-secret' not in str(q)
    with pytest.raises(ValueError): google.start_oauth()
    google.disconnect()
    assert google._pending is None and not google.connected()


def test_pending_calendar_proposal_cannot_cross_provider():
    from poda_app.agent import orchestrator
    p=service.propose_event({'title':'Review','start':'2027-02-05T14:00:00Z'})
    service.activate('notion')
    with pytest.raises(ValueError, match='Calendar'): orchestrator.approve(p['proposal_id'])
