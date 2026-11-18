import json
from pathlib import Path
import pytest
from poda_app.connectors.notion import mapping, presets, event_defaults
from poda_app.connectors.notion.client import NotionError


def test_portable_preset_and_completion():
    doc=json.loads(Path(presets.__file__).with_name('bestnds-v1.json').read_text())
    m=presets.validate_document(doc)
    assert m['date']=='Date'
    page={'properties':{'Name':'Deadline','Date':{'start':'2026-11-20'},'Type':'Task','Completed':True}}
    assert mapping.interpret_page(page,m)['done'] is True
    assert mapping.interpret_page({'properties':{'Completed':False}},m)['done'] is False


def test_import_rejects_credentials_and_source_date():
    with pytest.raises(NotionError):
        presets.validate_document({'format':presets.FORMAT,'version':1,'mapping':{'token':'secret'}})
    assert mapping.validate_mapping({'title':'Name','date':'Source Date'}, {'Name':{'type':'title'},'Date':{'type':'date'},'Source Date':{'type':'date'}})


def test_task_defaults_skip_attendance(monkeypatch):
    monkeypatch.setattr(event_defaults,'load',lambda _: {})
    schema={'Type':{'type':'select','select':{'options':[{'name':'Event'},{'name':'Task'}]}},'Commitment':{'type':'select','select':{'options':[{'name':'Committed'}]}}}
    props,_,_=event_defaults.resolve(schema,{},'source',{'type':'Task'})
    assert props['Type']['select']['name']=='Task'
    assert 'Commitment' not in props


@pytest.mark.parametrize('item_type,task', [('Event',False),('Task',True),('Homework',True),('Test',True),('Assignment',True)])
def test_type_wins_over_kind_source_and_completion(item_type, task):
    from poda_app.connectors.notion import calendar
    m=presets.validate_document(json.loads(Path(presets.__file__).with_name('bestnds-v1.json').read_text()))
    page={'properties':{'Name':'Example','Date':{'start':'2026-11-20'},'Type':item_type,
                        'Completed':True,'Task kind':'Homework','Event kind':'Meeting',
                        'Commitment':'Committed','Import Source':'Canvas'}}
    item=mapping.interpret_item(page,m)
    assert item['is_deadline'] is task
    assert item['done'] is True
    assert item['task_kind']==('Homework' if task else None)
    assert item['event_kind']==(None if task else 'Meeting')
    assert item['commitment']==(None if task else 'Committed')
    assert item['setup_warnings']
    fields={**m,'done_values':[]}
    result=calendar.interpret(page,fields)
    assert result['is_deadline'] is task
    assert result['done'] is True
    assert result['task_kind']==item['task_kind']


def test_custom_mapping_type_is_authoritative():
    page={'properties':{'Category':'Event','Type':'Task','Source':'Canvas','Task category':'Homework','Event category':'Meeting'}}
    m={'type':'Category','source':'Source','task_kind':'Task category','event_kind':'Event category','context_props':['Type']}
    result=mapping.interpret_item(page,m)
    assert result['kind']=='Event' and result['is_deadline'] is False
    assert result['task_kind'] is None and result['event_kind']=='Meeting'


def test_untyped_canvas_legacy_fallback_and_empty_kinds():
    from poda_app.connectors.notion import item_types
    assert item_types.interpret({'Import Source':'Canvas'}, {})['is_deadline'] is True
    assert item_types.interpret({'Type':'Event','Completed':True}, {})['setup_warnings']==[]


def kind_schema():
    return {'Name':{'type':'title'},'Date':{'type':'date'},'Completed':{'type':'checkbox'},
            **{n:{'type':'select','select':{'options':[{'name':v} for v in values]}} for n,values in
               {'Type':['Event','Task'],'Task kind':['Homework','Personal project'],'Event kind':['Meeting'], 'Commitment':['Committed']}.items()}}


def kind_mapping():
    return {'title':'Name','date':'Date','type':'Type','completed':'Completed','task_kind':'Task kind','event_kind':'Event kind','commitment':'Commitment','external_ids':{}}


@pytest.mark.parametrize('changes', [
    {'type':'Event','task_kind':'Homework'}, {'type':'Task','event_kind':'Meeting'},
    {'type':'Task','commitment':'Committed'}, {'type':'Task','task_kind':'invented option'},
])
def test_conflicting_or_unknown_writes_fail_before_api(changes):
    with pytest.raises(NotionError):
        mapping.build_properties(kind_mapping(),kind_schema(),{'title':'Example',**changes})


def test_matching_kinds_and_completion_write_and_clear():
    props=mapping.build_properties(kind_mapping(),kind_schema(),{'title':'Example','type':'Event','event_kind':'Meeting','task_kind':None,'completed':True})
    assert props['Completed']=={'checkbox':True}
    assert props['Event kind']=={'select':{'name':'Meeting'}}
    assert props['Task kind']=={'select':None}


def test_type_change_clears_opposite_fields_atomically(monkeypatch):
    from poda_app.connectors.notion import service
    m=kind_mapping(); schema=kind_schema();writes=[]
    raw={'id':'example','properties':{'Name':{'type':'title','title':[{'text':{'content':'Example'},'plain_text':'Example'}]},
         'Type':{'type':'select','select':{'name':'Event'}},'Event kind':{'type':'select','select':{'name':'Meeting'}},
         'Commitment':{'type':'select','select':{'name':'Committed'}},'Completed':{'type':'checkbox','checkbox':True}}}
    class Client:
        def update_page(self,page_id,payload):
            writes.append(payload)
            for name,value in payload['properties'].items():raw['properties'][name]={'type':schema[name]['type'],**value}
    monkeypatch.setattr(mapping,'require_write',lambda _: {'data_source_id':'source'})
    monkeypatch.setattr(mapping,'NotionClient',Client)
    monkeypatch.setattr(mapping,'live_mapping',lambda *_: ({'mapping':m},schema))
    monkeypatch.setattr(mapping,'page_in_source',lambda *_: raw)
    monkeypatch.setattr(mapping.receipts,'open_receipt',lambda *a,**kw: 'receipt')
    monkeypatch.setattr(mapping.receipts,'close_receipt',lambda *a,**kw: {})
    result=mapping.update_event('example',{'type':'Task','task_kind':'Personal project'},None)
    assert result['verified'] is True
    assert writes[0]['properties']=={'Type':{'select':{'name':'Task'}},'Task kind':{'select':{'name':'Personal project'}},'Event kind':{'select':None},'Commitment':{'select':None}}
    assert service.normalize_page(raw)['properties']['Completed'] is True


def test_unattended_edits_validate_stored_type(monkeypatch):
    from poda_app.connectors.notion import automation
    m=kind_mapping();schema=kind_schema();writes=[]
    raw={'id':'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','properties':{'Type':{'type':'select','select':{'name':'Task'}}}}
    class Client:
        def update_page(self,*args):writes.append(args)
    monkeypatch.setattr(automation,'NotionClient',Client)
    monkeypatch.setattr(mapping,'require_write',lambda _: {'data_source_id':'source'})
    monkeypatch.setattr(mapping,'live_mapping',lambda *_: ({'mapping':m},schema))
    monkeypatch.setattr(mapping,'page_in_source',lambda *_: raw)
    for change in ({'commitment':'Committed'},{'event_kind':'Meeting'}):
        result=automation.update_calendar_event(raw['id'],**change)
        assert result['ok'] is False and 'does not apply' in result['error']
    assert writes==[]
