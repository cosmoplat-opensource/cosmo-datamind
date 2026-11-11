"""Pinned source/design inputs and complete candidate JSON validation — DR-070."""
import json
from pathlib import Path

from rdflib import Graph
from rdflib.compare import isomorphic

from domains.robotics.modeling import abox, export, tbox
from domains.robotics.modeling.common import digest as json_digest
from ontology_assets.storage import digest
from srv_context import _RDF_LOCK
from .build_contracts import source_snapshot
from .build_service import BuildRequestError

OPERATIONS = ('design_tbox', 'extract_abox')
DESIGN_CONTRACT = {'id': 'robotics-design', 'version': 1}


def mapping_digest():
    from domains.robotics.modeling import _extractor
    return digest((Path(_extractor.__file__).parent / 'data' / 'xml_mapping.json').read_bytes())


def verify_input(storage, conn, run):
    fixed = run.get('design_input')
    if (not isinstance(fixed, dict) or run['operation'] not in OPERATIONS or run['mode'] != 'real'
            or any(run[k] is not None for k in ('build_input', 'ontology_id', 'version_id', 'ontology_snapshot'))):
        raise ValueError('设计运行输入不匹配。')
    try:
        current = source_snapshot(storage, conn, [s['source_asset_id'] for s in fixed['sources']])
        if (current != fixed['sources'] or {s['role']: s['sha256'] for s in current} != fixed['source_sha256']):
            raise ValueError('source mismatch')
    except (ValueError, OSError, KeyError) as exc:
        raise BuildRequestError('source_integrity', '原来源缺失或损坏，无法继续本次设计。', 409) from exc


def check_state(state):
    # Existing T05 invariants: frozen evidence/vocabulary/schema never self-authorize.
    tbox._check_state(state)
    if state['stage'] == 'abox':
        checked = tbox.confirm_schema({**state, 'stage': 'properties'}, snapshot_sha256=state['snapshot_sha256'])
        if checked['schema_confirmation'] != state['schema_confirmation']:
            raise ValueError('确认骨架不匹配。')


def artifact(name, kind, content, media_type='application/json'):
    return {'name': name, 'kind': kind, 'media_type': media_type, 'content': content}


def envelope(record, *, run=None, **extra):
    value = {'contract': DESIGN_CONTRACT, 'session_ref': record['session_ref'],
             'revision': record['state']['revision'], 'state': record['state'],
             'created_at': record['updated_at'], 'sources': record['sources']}
    if run is not None:
        value.update(run_id=run['run_id'], components=run['components'])
    return {**value, **extra}


def schema_for_export(state):
    result = state['result']
    if not result or not result.get('classes'):
        return None
    return {'namespace': state['namespace'], 'classes': result['classes'],
            'object_properties': result.get('object_properties', []),
            'data_properties': result.get('data_properties', [])}


def candidate_outcome():
    return {'status': 'designed', 'contract': dict(DESIGN_CONTRACT),
            'execution_readiness': 'not_verified', 'task_readiness': 'unsupported'}


def validate_candidate(document, ontology, run):
    """Validate real JSON/RDF bytes independently of stored completion flags."""
    state = document['state']
    check_state(state)
    fixed = run['design_input']
    if (run['operation'] != 'extract_abox' or state['stage'] != 'abox'
            or document['contract'] != DESIGN_CONTRACT
            or document['session_ref'] != run['session_ref']
            or document['revision'] != state['revision'] or state['revision'] != fixed['revision']
            or document['sources'] != fixed['sources']
            or state['source_sha256'] != fixed['source_sha256']
            or state['vocabulary']['sha256'] != fixed['vocabulary_sha256']
            or json_digest(state) != fixed['state_sha256']
            or document['components'] != run['components']
            or document['run_id'] != run['run_id']
            or document['payload_sha256'] != digest(ontology)
            or state['schema_confirmation']['sha256'] != fixed['confirmation_sha256']
            or document['coverage']['mapping_sha256'] != fixed['mapping_sha256']):
        raise ValueError('设计快照与固定运行不一致。')
    # A saved JSON must contain actual grounded assertions, not arbitrary valid RDF.
    schema = state['schema_confirmation']['tbox']
    checked = abox.validate_result(document['abox'], schema, state['evidence']['evidence'])
    expected = export.abox_owl(checked, schema, state['evidence']['evidence'])
    with _RDF_LOCK:
        if not isomorphic(Graph().parse(data=ontology, format='xml'), Graph().parse(data=expected, format='xml')):
            raise ValueError('设计 JSON 与 OWL 语义不一致。')
    if (document['validation'].get('contract') != DESIGN_CONTRACT
            or document['validation'].get('status') != 'designed'
            or document['validation'].get('execution_readiness') != 'not_verified'):
        raise ValueError('设计候选完成契约不匹配。')
    return json.loads(json.dumps(document))
