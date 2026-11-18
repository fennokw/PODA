"""Calendar boundary shared by chat, UI, reminders and automation.
Legacy notion_* tool names remain stable; permissions bind to provider and destination.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import json
import uuid
from threading import RLock
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from ...runtime.db import connect, get_setting, set_setting, now_iso

_lock=RLock()
_expected=ContextVar('calendar_destination',default=None)


@contextmanager
def bound(destination):
    token=_expected.set(destination)
    try: yield
    finally: _expected.reset(token)


def pin_destination(fn):
    @wraps(fn)
    def run(*args,**kwargs):
        with bound(binding()): return fn(*args,**kwargs)
    return run


def config():
    return json.loads(get_setting('calendar_provider_v1','') or '{"provider":"notion","allow_write":true}')


def capability():
    cfg=config(); provider=cfg['provider']
    if provider=='notion':
        from ..notion import state
        cap=dict(state.capability()); cap['provider']=provider
        if not cfg.get('allow_write',True): cap['can_insert_content']=cap['can_update_content']=False
        return cap
    if provider=='google':
        from . import google
        connected=google.connected() and bool(cfg.get('calendar_id')); dest=cfg.get('calendar_id','')
    else: connected=True; dest='local'
    write=bool(connected and cfg.get('allow_write'))
    return {'provider':provider,'connected':connected,'can_read_content':connected,'can_insert_content':write,
            'can_update_content':write,'mapping_active':connected,'data_source_id':dest,
            'database_title':cfg.get('name') or 'PODA Calendar','status':'CONNECTED' if connected else 'DISCONNECTED'}


def binding():
    cap=capability()
    return {'provider':cap['provider'],'data_source_id':cap.get('data_source_id') or ''}


def matches(scope):
    live=binding()
    return scope.get('provider','notion')==live['provider'] and scope.get('data_source_id')==live['data_source_id']


def activate(provider,calendar_id='',allow_write=True):
    if provider not in {'notion','local','google'}: raise ValueError('Choose Notion, PODA local, or Google Calendar.')
    name='Notion' if provider=='notion' else 'PODA Calendar'
    if provider=='google':
        from . import google
        chosen=next((c for c in google.calendars() if c['id']==calendar_id),None)
        if not chosen: raise ValueError('Choose an accessible Google calendar.')
        if allow_write and chosen.get('accessRole') not in {'writer','owner'}: raise ValueError('This calendar is read-only. Turn editing off.')
        name=chosen.get('summary',calendar_id)
    with _lock:
        set_setting('calendar_provider_v1',json.dumps({'provider':provider,'calendar_id':calendar_id if provider=='google' else '',
                                                      'name':name,'allow_write':bool(allow_write)}))
    return capability()


def _require(write=False, permission="can_insert_content"):
    cap=capability()
    if _expected.get() is not None and not matches(_expected.get()): raise ValueError('The selected calendar changed during this request. Retry for the current calendar.')
    if not cap.get('can_read_content') or (write and not cap.get(permission)):
        raise ValueError('Connect your selected calendar and enable its required access in Calendar.')
    return cap


def _db():
    db=connect()
    db.execute('CREATE TABLE IF NOT EXISTS local_calendar_events (id TEXT PRIMARY KEY,payload TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0)')
    db.commit(); return db


def validate(title,start,end='',notes=''):
    if not title.strip() or len(title)>160 or len(notes)>1800: raise ValueError('Enter a title of 1–160 characters and notes of at most 1800 characters.')
    dt=datetime.fromisoformat(start.replace('Z','+00:00'))
    if 'T' not in start:
        date.fromisoformat(start)
        if end and date.fromisoformat(end)<date.fromisoformat(start): raise ValueError('End must not precede start.')
        return end or start
    if not dt.tzinfo: raise ValueError('Start needs a timezone offset.')
    stop=datetime.fromisoformat(end.replace('Z','+00:00')) if end else dt+timedelta(hours=1)
    if not stop.tzinfo or stop<=dt: raise ValueError('End must follow start and include a timezone offset.')
    return stop.isoformat()


def _failure(exc): return {'ok':False,'error':str(exc),'result':None,'verification':'not confirmed'}


def create_calendar_event(title,start,end='',notes='',commitment='',area=''):
    try:
        with _lock:
            cap=_require(True)
            if cap['provider']=='notion':
                from ..notion.automation import create_calendar_event as create
                return create(title,start,end,notes,commitment,area)
            stop=validate(title,start,end,notes)
            provisional={k:v for k,v in {'commitment':'Maybe' if not commitment else '', 'area':'Personal' if not area else '',
                                         'duration':'1 hour' if not end and 'T' in start else ''}.items() if v}
            from ..notion.event_defaults import review
            event={'page_id':uuid.uuid4().hex,'title':title.strip(),'start':start,'end':stop,'notes':notes,
                   'commitment':commitment or 'Maybe','area':[area or 'Personal'],'type':'Event','all_day':'T' not in start,
                   'provisional_fields':provisional,'review_question':review(provisional,[])}
            if cap['provider']=='google':
                from . import google
                return google.create(event)
            db=_db()
            try:
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute('SELECT payload FROM local_calendar_events WHERE archived=0'):
                    old=json.loads(row['payload'])
                    if old['title'].casefold()==title.strip().casefold() and datetime.fromisoformat(old['start'].replace('Z','+00:00'))==datetime.fromisoformat(start.replace('Z','+00:00')):
                        return {'ok':True,'result':{'created':False,'duplicate':True,'matches':[old]},'verification':'existing local event'}
                db.execute('INSERT INTO local_calendar_events(id,payload) VALUES (?,?)',(event['page_id'],json.dumps(event))); db.commit()
                saved=json.loads(db.execute('SELECT payload FROM local_calendar_events WHERE id=?',(event['page_id'],)).fetchone()['payload'])
                return {'ok':True,'result':{**saved,'created':True,'verified':True},'verification':'local re-read-after-write'}
            finally: db.close()
    except Exception as exc: return _failure(exc)


def update_calendar_event(page_id,title='',start='',end='',commitment='',area='',completed=None,task_kind=None,event_kind=None):
    try:
        if not page_id: raise ValueError('Choose an event to edit.')
        with _lock:
            cap=_require(True, "can_update_content")
            if cap['provider']=='notion':
                from ..notion.automation import update_calendar_event as update
                extra = {k:v for k,v in {'completed':completed,'task_kind':task_kind,'event_kind':event_kind}.items() if v is not None}
                return update(page_id,title,start,end,commitment,area,**extra)
            if task_kind is not None or event_kind is not None:
                raise ValueError('Kind properties currently require a Notion calendar.')
            if completed is not None:
                raise ValueError('Completion checkboxes currently require a Notion calendar.')
            if cap['provider']=='google':
                from . import google
                return google.update(page_id,title,start,end,commitment,area)
            db=_db()
            try:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT payload FROM local_calendar_events WHERE id=? AND archived=0',(page_id,)).fetchone()
                if not row: raise ValueError('Event not found in the selected calendar.')
                ev=json.loads(row['payload'])
                for k,v in {'title':title,'start':start,'end':end,'commitment':commitment,'area':[area] if area else []}.items():
                    if v: ev[k]=v
                validate(ev['title'],ev['start'],ev['end'],ev.get('notes','')); ev['all_day']='T' not in ev['start']
                for k,v in {'commitment':commitment,'area':area,'duration':end}.items():
                    if v: ev.get('provisional_fields',{}).pop(k,None)
                db.execute('UPDATE local_calendar_events SET payload=? WHERE id=?',(json.dumps(ev),page_id)); db.commit()
                return {'ok':True,'result':{**ev,'updated':True,'verified':True},'verification':'local committed write'}
            finally: db.close()
    except Exception as exc: return _failure(exc)


def archive_calendar_event(page_id):
    try:
        if not page_id: raise ValueError('Choose an event to archive.')
        with _lock:
            cap=_require(True, "can_update_content")
            if cap['provider']=='notion':
                from ..notion.automation import archive_calendar_event as archive
                return archive(page_id)
            if cap['provider']=='google':
                from . import google
                return google.archive(page_id)
            db=_db()
            try:
                changed=db.execute('UPDATE local_calendar_events SET archived=1 WHERE id=?',(page_id,)).rowcount;db.commit()
                if not changed: raise ValueError('Event not found in the selected calendar.')
                return {'ok':True,'result':{'archived':True,'page_id':page_id},'verification':'local committed write'}
            finally:db.close()
    except Exception as exc:return _failure(exc)


def events_between(start,end):
    with _lock:
        cap=_require();first,last=date.fromisoformat(start),date.fromisoformat(end)
        if not 0<=(last-first).days<=120: raise ValueError('Choose a range of up to 120 days.')
        if cap['provider']=='notion':
            from ..notion.calendar import events_between as fetch
            return fetch(start,end)
        if cap['provider']=='google':
            from . import google
            events=google.events(start,end)
        else:
            db=_db()
            try: events=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM local_calendar_events WHERE archived=0')]
            finally:db.close()
    events=[e for e in events if e['start'][:10]<=(last+timedelta(days=1)).isoformat() and
            (e.get('end') or e['start'])[:10]>=(first-timedelta(days=1)).isoformat()]
    events.sort(key=lambda e:e['start'])
    return {'events':events,'count':len(events),'fields':{},'start':start,'end':end,'provider':cap['provider']}


def items_between_effective(start,end,include_cancelled=False,tz_name='America/New_York'):
    if config()['provider']=='notion':
        from ..notion.mapping import items_between_effective as fetch
        return fetch(start,end,include_cancelled,tz_name)
    zone=ZoneInfo(tz_name);items=[]
    for ev in events_between(start,end)['events']:
        all_day=ev.get('all_day',False)
        beginning=datetime.fromisoformat(ev['start'].replace('Z','+00:00'))
        stopping=datetime.fromisoformat((ev.get('end') or ev['start']).replace('Z','+00:00'))
        if not all_day:beginning=beginning.astimezone(zone);stopping=stopping.astimezone(zone)
        if stopping.date().isoformat()<start or beginning.date().isoformat()>end:continue
        clock='All day' if all_day else beginning.strftime('%H:%M')+'–'+stopping.strftime('%H:%M')
        items.append({**ev,'local_date':beginning.date().isoformat(),'start_local':beginning.isoformat(),'end_local':stopping.isoformat(),
                      'date_start':ev['start'],'date_end':ev.get('end'),'start_time':None if all_day else beginning.strftime('%H:%M'),
                      'end_time':None if all_day else stopping.strftime('%H:%M'),'areas':ev.get('area',[]),'kind':'event','is_deadline':False,
                      'summary':f"{beginning.date()} {clock} — {ev['title']}"})
    return {'items':items,'count':len(items),'time_zone':tz_name,'mapping':{'date':'start','inferred':False}}


@pin_destination
def propose_event(event,session_id=None):
    if config()['provider']=='notion':
        from ..notion.mapping import propose_event as propose
        return propose(event,session_id)
    from ...agent import orchestrator,tools
    _require(True)
    args={k:event[k] for k in ('title','start','end','notes','commitment','area') if event.get(k)}
    validate(args['title'],args['start'],args.get('end',''),args.get('notes',''))
    ident=str(uuid.uuid4());step={'tool':'notion_calendar_create','args':args,'mutating':True,'requires_confirmation':True,
                                'summary':tools.summarize_args('notion_calendar_create',args)}
    plan={'kind':'calendar_create_event','message':'Add extracted email event','steps':[step],'calendar_binding':binding()}
    db=connect()
    try:
        orchestrator.ensure_tables(db)
        db.execute('INSERT INTO agent_proposals(id,session_id,created_at,status,plan_json,receipt_ids_json) VALUES (?,?,?,?,?,?)',
                   (ident,session_id,now_iso(),'pending',json.dumps(plan),'[]'));db.commit()
    finally:db.close()
    return {'proposal_id':ident,'payload_preview':args,'requires_confirmation':True,'duplicates':[],
            'target':capability().get('database_title'),'warning':'Duplicates will be checked before creation.'}


def commit_event(proposal_id):
    from ...agent import orchestrator
    proposal=orchestrator.get_proposal(proposal_id)
    if not proposal:raise ValueError('Proposal not found.')
    if proposal.get('plan',{}).get('kind')=='notion_create_event':
        with _lock:
            _require(True)
            if config()['provider']!='notion':raise ValueError('The selected calendar changed. Prepare a new proposal.')
            from ..notion.mapping import commit_event as commit
            return commit(proposal_id)
    if proposal.get('plan',{}).get('kind')!='calendar_create_event':raise ValueError('Review this proposal in Chat.')
    result=orchestrator.approve(proposal_id)
    if result['status']!='executed':raise ValueError('Calendar creation was not verified. Check the receipt before retrying.')
    return {**result['results'][0].get('result',{}),'receipt':result['receipts'][0]}
