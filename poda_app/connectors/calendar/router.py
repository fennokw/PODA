"""Session-protected calendar setup and event editing."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from . import service,google
router=APIRouter(prefix='/calendar')


def call(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except Exception as exc:raise HTTPException(400,detail={'code':'calendar_setup','message':str(exc)}) from exc


def result(value):
    if not value.get('ok'):raise HTTPException(400,detail={'code':'calendar_write','message':value.get('error') or 'Write not verified','result':value.get('result')})
    return value


class Selection(BaseModel):
    provider:str
    calendar_id:str=''
    allow_write:bool=True
class Event(BaseModel):
    title:str
    start:str
    end:str=''
    notes:str=''
    commitment:str=''
    area:str=''
class Edit(BaseModel):
    task_kind:str|None=None
    event_kind:str|None=None
    completed:bool|None=None
    title:str=''
    start:str=''
    end:str=''
    commitment:str=''
    area:str=''
class Defaults(BaseModel):
    values:dict[str,str]=Field(default_factory=dict)
class Commit(BaseModel):
    proposal_id:str
    confirm:bool=False


@router.get('/status')
def status():return {'config':service.config(),'capability':service.capability(),'google':google.status()}
@router.put('/provider')
def select(req:Selection):return call(service.activate,**req.model_dump())
@router.get('/events')
def events(start:str,end:str):return call(service.events_between,start,end)
@router.post('/events')
def create(req:Event):return result(service.create_calendar_event(**req.model_dump()))
@router.patch('/events/{event_id}')
def edit(event_id:str,req:Edit):return result(service.update_calendar_event(event_id,**req.model_dump()))
@router.delete('/events/{event_id}')
def delete(event_id:str):return result(service.archive_calendar_event(event_id))
@router.post('/commit')
def commit(req:Commit):
    if not req.confirm:raise HTTPException(400,'Confirm the proposed calendar event.')
    return call(service.commit_event,req.proposal_id)
@router.post('/google/client')
def client(document:dict):call(google.configure,document);return {'ok':True}
@router.post('/google/connect')
def connect_google():return call(google.start_oauth)
@router.post('/google/disconnect')
def disconnect_google():call(google.disconnect);return {'ok':True}
@router.get('/google/calendars')
def google_calendars():return {'calendars':call(google.calendars)}


def notion_defaults():
    from ..notion import mapping,state,event_defaults
    cfg=state.get_config() or {};schema=cfg.get('schema',{});names=(mapping.get_active_mapping() or {}).get('mapping',{})
    return cfg,schema,names,event_defaults.fields(schema,names)


@router.get('/notion/defaults')
def get_defaults():
    from ..notion import event_defaults
    cfg,schema,names,fields=notion_defaults()
    try:chosen,_,missing=event_defaults.resolve(schema,names,cfg.get('data_source_id',''))
    except ValueError as exc:chosen,missing={},[str(exc)]
    return {'date_property':names.get('date'),'source_id':cfg.get('data_source_id'),'values':event_defaults.load(cfg.get('data_source_id','')),
            'suggested':{k:(chosen[n][schema[n]['type']][0]['name'] if schema[n]['type']=='multi_select' else chosen[n][schema[n]['type']]['name']) for k,n in fields.items() if n in chosen},
            'fields':[{'key':k,'name':n,'options':event_defaults.options(schema[n])} for k,n in fields.items() if n],'missing':missing}


@router.put('/notion/defaults')
def save_defaults(req:Defaults):
    from ..notion import event_defaults
    cfg,schema,names,_=notion_defaults()
    if not cfg.get('data_source_id'):raise HTTPException(400,'Connect Notion first.')
    call(event_defaults.save,cfg['data_source_id'],schema,names,req.values);return {'ok':True}


@router.post('/notion/events/{event_id}/fill-missing')
def fill_missing(event_id:str):
    from ..notion.automation import fill_missing_event_fields
    with service._lock:
        if service.config()['provider']!='notion':raise HTTPException(400,'Select Notion first.')
        return result(fill_missing_event_fields(event_id))
