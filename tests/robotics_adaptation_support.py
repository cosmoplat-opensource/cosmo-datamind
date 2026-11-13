"""IR-026 pure-core fixtures: real XML/evidence/ABox, no application implementation — DR-072."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from rdflib import Graph

from tests.robotics_modeling_support import api as modeling, bundle, confirmation, digest, ur_schema
from tests.robotics_support import SOURCE, MODELS

MODULE = 'domains.robotics.application_adaptation'
PUBLIC = {'Perception': {'position', 'exposure_time', 'model_name', 'threshold', 'object_name'},
          'Place': {'place_pose'}}
EXECUTION_CLASSES = {'Skill', 'AtomicSkill', 'CompositeSkill', 'TaskProcedure', 'SkillImplementation',
                     'PortContract', 'SkillParameter', 'SkillInvocation', 'ParameterBinding', 'DataReference',
                     'ImplementationConfiguration'}
EXECUTION_RELATIONS = {'hasStep', 'invokesSkill', 'hasInput', 'hasOutput', 'implementedBy', 'hasPortContract',
                      'mapsToPort', 'hasBinding', 'bindsParameter', 'sourceParameter', 'sourceData', 'targetData',
                      'parameterData', 'hasFixedConfiguration', 'hasFixedBinding'}


def api():
    # Import during execution so missing target capability is a failed case, never collection error/skip.
    return importlib.import_module(MODULE)


def role_map(tbox, *, confirmed=True):
    return {'version': '1', 'tbox_sha256': digest(tbox), 'confirmed': confirmed,
        'bindings': [{'kind': kind, 'role': row['id'], 'iri': tbox['namespace'] + row['id']}
                     for kind in ('classes', 'object_properties', 'data_properties') for row in tbox[kind]]}


def design(tbox=None, values=None):
    schema = ur_schema() if tbox is None else deepcopy(tbox)
    evidence = bundle(values)
    confirmed = confirmation(schema, evidence)
    extracted = modeling('abox').extract(confirmed, evidence)
    raw = modeling('export').abox_owl(extracted['result'], schema, evidence['evidence'])
    return {'tbox': schema, 'confirmation': confirmed, 'abox': extracted['result'],
            'coverage': extracted['report'], 'evidence': evidence, 'ontology': raw,
            'origin': {'ontology_id': 'ont-synthetic', 'version_id': 'ver-design-1',
                       'payload_sha256': hashlib.sha256(raw).hexdigest()}}


@pytest.fixture
def sample():
    return design()


def extraction_input(change=None):
    tree, models = ET.parse(SOURCE).getroot(), ET.parse(MODELS).getroot()
    if change is not None:
        change(tree, models)
    evidence = bundle({'behavior_tree': ET.tostring(tree), 'node_models': ET.tostring(models)})
    schema = ur_schema()
    return {'confirmation': confirmation(schema, evidence), 'evidence_bundle': evidence,
            'role_map': role_map(schema)}


def extract(change=None):
    return api().extract_execution(**extraction_input(change))


def adapt(sample=None):
    value = design() if sample is None else sample
    return api().adapt(design=value, role_map=role_map(value['tbox']), mode='real')


def graph(raw):
    assert isinstance(raw, bytes)
    return Graph().parse(data=raw, format='xml')


def blocked(output, code):
    assert output['status'] == 'blocked'
    issues = [i for i in output['issues'] if i['code'] == code]
    assert issues, output
    assert all(i['message'] and i['expected'] is not None and 'observed' in i for i in issues)
    return issues


def issue_sources(issues, evidence):
    known = {e['id'] for e in evidence['evidence']}
    assert all(i['source_refs'] and set(i['source_refs']) <= known for i in issues)


def calls(output, tree_id):
    assert output['status'] == 'extracted', output
    return next(t['calls'] for t in output['execution']['trees'] if t['tree_id'] == tree_id)


def exported(product):
    return api().export_bundle(product, output_asset_ref={'ontology_id': 'ont-synthetic', 'version_id': 'ver-app-2'},
                               parent_version_id='ver-design-1')


def frozen(value):
    # Input contains bytes; a recursive deep copy preserves their exact meaning.
    return deepcopy(value)


def old_test_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (SOURCE, MODELS, Path('domains/robotics/modeling/data/xml_mapping.json'))}


def json_document(files):
    assert set(files) == {'owl', 'json'}
    assert isinstance(files['owl'], bytes) and isinstance(files['json'], bytes)
    return json.loads(files['json'])
