"""Google Calendar installed-app OAuth: OS credential storage, loopback state + PKCE.
Calendar API calls use the egress guard; inference remains local.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from datetime import date,datetime,timedelta
from http.server import BaseHTTPRequestHandler,HTTPServer
from urllib.parse import urlencode,urlparse,parse_qs,quote
from ...security import keychain,egress

_SERVICE='google-calendar'
_SCOPES='https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly'
_lock=threading.RLock()
_pending=None
_flow_status='idle'


def _get(account):return json.loads(keychain.keychain_get(_SERVICE,account) or '{}')
def _save(account,value):keychain.keychain_set(_SERVICE,account,json.dumps(value))
def connected():return bool(_get('tokens').get('refresh_token'))
def status():return {'connected':connected(),'client_configured':bool(_get('client').get('client_id')),'oauth_status':_flow_status}


def configure(document):
    client=document.get('installed') or {}
    if not str(client.get('client_id','')).endswith('.apps.googleusercontent.com') or not client.get('client_secret'):
        raise ValueError('Upload a Google Desktop app OAuth client JSON, not a web app or service account.')
    disconnect();_save('client',{'client_id':client['client_id'],'client_secret':client['client_secret']})


def _token(data):
    r=egress.guarded_request('google_calendar','POST','https://oauth2.googleapis.com/token',form_body=data,purpose='Google Calendar authorization')
    if not r.ok:raise ValueError('Google authorization failed. Reconnect and check the OAuth client and consent settings.')
    return r.json()


def start_oauth():
    global _pending,_flow_status
    if egress.offline_mode():raise ValueError('Turn off Offline mode to connect Google Calendar.')
    client=_get('client')
    if not client:raise ValueError('Upload your Google Desktop OAuth client JSON first.')
    with _lock:
        if _pending:raise ValueError('Google sign-in is already open. Finish it or disconnect to cancel.')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        flow={'state':state,'verifier':verifier,'expires':time.monotonic()+300}
        class Callback(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                global _pending,_flow_status
                parsed=urlparse(self.path);args=parse_qs(parsed.query)
                with _lock:
                    valid=(_pending is flow and time.monotonic()<flow['expires'] and parsed.path=='/callback'
                           and secrets.compare_digest(args.get('state',[''])[0],state))
                    if not valid:self.send_error(400,'Expired or invalid sign-in request');return
                    _pending=None
                    try:
                        if 'error' in args or not args.get('code'):raise ValueError('Sign-in was not approved.')
                        result=_token({**client,'code':args['code'][0],'code_verifier':verifier,'redirect_uri':flow['redirect'],'grant_type':'authorization_code'})
                        if not result.get('refresh_token'):raise ValueError('Offline access was not granted.')
                        if not set(_SCOPES.split()).issubset(set(result.get('scope','').split())):raise ValueError('Calendar scopes were not granted.')
                        _save('tokens',{**result,'expires_at':time.time()+result.get('expires_in',3600)})
                        _flow_status='connected';message='Google connected. Return to PODA, refresh calendars and select one to activate.'
                    except Exception:
                        _flow_status='failed';message='Google sign-in failed or was cancelled. Return to PODA and try again.'
                self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.end_headers();self.wfile.write(message.encode())
        server=HTTPServer(('127.0.0.1',0),Callback);server.timeout=1
        flow['redirect']=f'http://127.0.0.1:{server.server_port}/callback'
        _pending=flow;_flow_status='waiting'
        def listen():
            global _pending,_flow_status
            try:
                while _pending is flow and time.monotonic()<flow['expires']:server.handle_request()
            finally:
                server.server_close()
                with _lock:
                    if _pending is flow:_pending=None;_flow_status='expired'
        threading.Thread(target=listen,daemon=True).start()
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':client['client_id'],
            'redirect_uri':flow['redirect'],'response_type':'code','scope':_SCOPES,'access_type':'offline','prompt':'consent',
            'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})}


def disconnect():
    global _pending,_flow_status
    with _lock:_pending=None;_flow_status='idle';keychain.keychain_delete(_SERVICE,'tokens')


def _request(method,path,body=None):
    with _lock:
        tokens=_get('tokens')
        if not tokens.get('refresh_token'):raise ValueError('Connect Google Calendar first.')
        if tokens.get('expires_at',0)<=time.time()+60:
            refreshed=_token({**_get('client'),'refresh_token':tokens['refresh_token'],'grant_type':'refresh_token'})
            tokens.update(refreshed);tokens['expires_at']=time.time()+refreshed.get('expires_in',3600);_save('tokens',tokens)
    try:
        r=egress.guarded_request('google_calendar',method,'https://www.googleapis.com/calendar/v3/'+path,
                                headers={'Authorization':'Bearer '+tokens['access_token']},json_body=body,purpose='Google Calendar '+method)
    except Exception as exc:
        raise ValueError('Google Calendar could not be reached. '+('The write may have completed; check before retrying.' if method!='GET' else 'Check Offline mode and your connection.')) from exc
    if not r.ok:raise ValueError(f'Google Calendar returned HTTP {r.status_code}. Check access or reconnect; no automatic write retry was made.')
    return r.json() if r.content else {}


def _pages(path):
    out=[];token=''
    for _ in range(100):
        page=_request('GET',path+('&' if '?' in path else '?')+urlencode({'pageToken':token}) if token else path)
        out.extend(page.get('items',[]));token=page.get('nextPageToken','')
        if not token:return out
    raise ValueError('Calendar result is too large; request a smaller range.')


def calendars():
    return [{'id':c['id'],'summary':c.get('summary',c['id']),'accessRole':c.get('accessRole'),'primary':c.get('primary',False)}
            for c in _pages('users/me/calendarList?maxResults=250') if not c.get('deleted') and c.get('accessRole') in {'owner','writer','reader'}]


def _path(event_id=''):
    from . import service
    cfg=service.config()
    if cfg['provider']!='google' or not cfg.get('calendar_id'):raise ValueError('Google Calendar is not active.')
    return 'calendars/'+quote(cfg['calendar_id'],safe='')+'/events'+('/'+quote(event_id,safe='') if event_id else '')


def _event(raw):
    meta=(raw.get('extendedProperties') or {}).get('private',{})
    start=raw['start'].get('dateTime') or raw['start']['date'];end=raw.get('end',{}).get('dateTime') or raw.get('end',{}).get('date')
    all_day='date' in raw['start']
    if all_day and end:end=(date.fromisoformat(end)-timedelta(days=1)).isoformat()
    return {'page_id':raw['id'],'title':raw.get('summary') or 'Untitled','start':start,'end':end,'all_day':all_day,
            'notes':raw.get('description',''),'url':raw.get('htmlLink'),'commitment':meta.get('poda_commitment'),
            'area':[meta['poda_area']] if meta.get('poda_area') else [],'type':'Event'}


def events(start,end):
    first=date.fromisoformat(start)-timedelta(days=1);last=date.fromisoformat(end)+timedelta(days=2)
    query=urlencode({'timeMin':first.isoformat()+'T00:00:00Z','timeMax':last.isoformat()+'T00:00:00Z','singleEvents':'true','orderBy':'startTime','maxResults':2500})
    return [_event(e) for e in _pages(_path()+'?'+query) if e.get('status')!='cancelled']


def _verify(ident,expected,result):
    try:
        raw=_request('GET',_path(ident));ok=True
        for key,value in expected.items():
            if key in {'start','end'} and 'date' in value:ok &= raw.get(key,{}).get('date')==value['date']
            elif key in {'start','end'}:ok &= datetime.fromisoformat(raw[key]['dateTime'].replace('Z','+00:00'))==datetime.fromisoformat(value['dateTime'].replace('Z','+00:00'))
            elif key=='extendedProperties':ok &= all(raw.get(key,{}).get('private',{}).get(k)==v for k,v in value['private'].items())
            else:ok &= raw.get(key)==value
        return {'ok':ok,'result':{**_event(raw),**result,'verified':ok},'error':None if ok else 'Google re-read differed. Check before retrying.','verification':'re-read-after-write'}
    except Exception:return {'ok':False,'result':{**result,'page_id':ident,'verified':False},'error':'Google accepted the write but verification failed. Check before retrying.'}


def create(event):
    for old in events(event['start'][:10],event['start'][:10]):
        if old['title'].casefold()==event['title'].casefold() and datetime.fromisoformat(old['start'].replace('Z','+00:00'))==datetime.fromisoformat(event['start'].replace('Z','+00:00')):
            return {'ok':True,'result':{'duplicate':True,'created':False,'matches':[old]},'verification':'existing Google event'}
    body={'id':event['page_id'],'summary':event['title'],'description':event['notes'],'start':{'dateTime':event['start']},'end':{'dateTime':event['end']},
          'extendedProperties':{'private':{'poda_commitment':event['commitment'],'poda_area':event['area'][0]}}}
    if event['all_day']:
        body['start']={'date':event['start']};body['end']={'date':(date.fromisoformat(event['end'])+timedelta(days=1)).isoformat()}
    saved=_request('POST',_path()+'?sendUpdates=none',body)
    return _verify(saved['id'],body,{'created':True,'provisional_fields':event['provisional_fields'],'review_question':event['review_question']})


def update(ident,title='',start='',end='',commitment='',area=''):
    from .service import validate
    old=_request('GET',_path(ident));data={}
    if title:
        if not title.strip() or len(title)>160:raise ValueError('Invalid title.')
        data['summary']=title.strip()
    if start or end:
        current=_event(old);s=start or current['start'];e=end or current.get('end','')
        stop=validate(title or old.get('summary','Untitled'),s,e)
        if 'T' not in s:data.update(start={'date':s},end={'date':(date.fromisoformat(stop)+timedelta(days=1)).isoformat()})
        else:data.update(start={'dateTime':s},end={'dateTime':stop})
    if commitment or area:
        meta=dict((old.get('extendedProperties') or {}).get('private',{}))
        if commitment:meta['poda_commitment']=commitment
        if area:meta['poda_area']=area
        data['extendedProperties']={'private':meta}
    if not data:raise ValueError('Specify a field to update.')
    _request('PATCH',_path(ident)+'?sendUpdates=none',data)
    return _verify(ident,data,{'updated':True})


def archive(ident):
    _request('DELETE',_path(ident)+'?sendUpdates=none')
    return {'ok':True,'result':{'archived':True,'page_id':ident},'verification':'Google accepted deletion'}
