"""Portable, connection-free schema mappings. Never imports tokens or page IDs."""
import json
from pathlib import Path
from . import mapping
from .client import NotionError

FORMAT = 'poda-notion-mapping'
VERSION = 1
MAX_BYTES = 65536
SCALAR_KEYS = set(mapping.MAPPING_FIELDS) | {'default_status','default_type','default_commitment','default_area','default_source','preset'}


def validate_document(document):
    if len(json.dumps(document).encode()) > MAX_BYTES:
        raise NotionError('validation', 'Mapping files must be at most 64 KB.')
    if document.get('format') != FORMAT or document.get('version') != VERSION:
        raise NotionError('validation', 'Unsupported mapping format or version. Export a PODA mapping JSON file.')
    value = document.get('mapping')
    if not isinstance(value, dict) or set(value) - (SCALAR_KEYS | {'external_ids','status_done_values'}):
        raise NotionError('validation', 'Mapping contains unsupported fields. Connection credentials and IDs cannot be imported.')
    for key in SCALAR_KEYS:
        if value.get(key) is not None and (not isinstance(value[key], str) or len(value[key]) > 200):
            raise NotionError('validation', f'Invalid mapping value: {key}.')
    ext = value.get('external_ids', {})
    done = value.get('status_done_values', [])
    if not isinstance(ext, dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>200 or len(v)>200 for k,v in ext.items()):
        raise NotionError('validation', 'External IDs must map field names to property names.')
    if not isinstance(done,list) or any(not isinstance(v,str) or len(v)>200 for v in done):
        raise NotionError('validation', 'Done values must be a list of names.')
    return value


def import_mapping(document):
    return mapping.save_mapping(validate_document(document))


def export_mapping():
    active = mapping.get_active_mapping()
    if not active:
        raise NotionError('validation', 'Save a mapping before exporting.')
    allowed = SCALAR_KEYS | {'external_ids','status_done_values'}
    return {'format': FORMAT, 'version': VERSION, 'mapping': {k:v for k,v in active['mapping'].items() if k in allowed}}


def apply_bestnds():
    document = json.loads(Path(__file__).with_name('bestnds-v1.json').read_text())
    # save_mapping retrieves the live destination schema and versions the change atomically.
    result = import_mapping(document)
    from . import event_defaults, state
    source = state.get_config()['data_source_id']
    event_defaults.save(source,state.get_config()['schema'],result['mapping'],
                        {'type':'Event','commitment':'Committed','area':'General','source':'Manual'})
    return result
