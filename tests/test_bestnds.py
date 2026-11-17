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
