"""Schema-valid new-event defaults, scoped to the connected Notion data source."""
import json
from . import item_types
from ...runtime.db import get_setting, set_setting

PREFERENCES = {'type':('Event','Meeting','Appointment','Calendar'), 'commitment':('Maybe','Tentative','Yes'),
               'area':('Personal','General','Other','Life'), 'status':('Active','Not started','To do','Scheduled'),
               'source':('PODA','Calendar','Manual')}
CANDIDATES = {'type':('Type','Kind'), 'commitment':('Commitment',), 'area':('Area','Areas'),
              'status':('Status','Source Status'), 'source':('Import Source','Source')}


def options(prop):
    return [o['name'] if isinstance(o,dict) else o for o in
            (prop.get(prop.get('type')) or {}).get('options',prop.get('options',[]))]


def load(source):
    return json.loads(get_setting('calendar_notion_defaults:'+source,'{}') or '{}')


def fields(schema,names):
    return {k:next((n for n in (names.get(k),*candidates) if n and
                    schema.get(n,{}).get('type') in {'select','multi_select','status'}),None)
            for k,candidates in CANDIDATES.items()}


def resolve(schema,names,source,explicit=None):
    explicit=explicit or {}; saved=load(source); props={}; provisional={}; missing=[]
    effective_type = explicit.get('type') or saved.get('type') or 'Event'
    item_types.validate_changes(effective_type, explicit)
    is_task = item_types.is_task(effective_type)
    for key,name in fields(schema,names).items():
        if is_task and key == 'commitment':
            continue
        if not name:
            if key in {'area','commitment'} and any(n in schema for n in CANDIDATES[key]):
                missing.append(key+' needs a supported select property or a related page chosen in Notion')
            if explicit.get(key): raise ValueError(f'No editable {key} property is configured')
            continue
        prop=schema[name]; choices=options(prop)
        wanted=explicit.get(key) or saved.get(key) or (names.get('default_'+key) if key!='type' else None)
        value=next((v for v in choices if wanted and v.casefold()==wanted.casefold()),None)
        if wanted and not value: raise ValueError(f'{name} has no option named {wanted}. Review Calendar defaults.')
        if not value:
            value=next((v for preferred in PREFERENCES[key] for v in choices if v.casefold()==preferred.casefold()),None)
            if not value and key in {'area','commitment','type','source'}:
                value=next((v for v in choices if v.casefold() not in {'no','declined','cancelled','canceled','done','completed','archived'}),None)
        if not value: missing.append(name); continue
        kind=prop['type']; props[name]={kind:[{'name':value}] if kind=='multi_select' else {'name':value}}
        if not explicit.get(key) and key in {'area','commitment'}: provisional[key]=value
    return props,provisional,missing


def save(source,schema,names,values, *, conn=None):
    known=fields(schema,names)
    for key,value in values.items():
        name = known.get(key)
        if not name or value not in options(schema[name]):
            raise ValueError(f'Notion needs the option "{value}" in "{name or key}". Add it to the connected database or map your calendar fields manually.')
    set_setting('calendar_notion_defaults:'+source, json.dumps(values), conn=conn)


def review(provisional,missing):
    parts=[]
    if provisional: parts.append('I used temporary defaults: '+', '.join(f'{k}: {v}' for k,v in provisional.items())+'. Would you like to change these?')
    if missing: parts.append('Please review these fields in Notion: '+', '.join(missing)+'.')
    return ' '.join(parts)
