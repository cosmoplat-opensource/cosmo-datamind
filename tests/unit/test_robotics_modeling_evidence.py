"""Evidence occurrence identity and bundled vocabulary contracts — DR-152 / T04 RED."""
from copy import deepcopy
import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from tests.robotics_modeling_support import api, bundle, sources, no_network, FIXTURES

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_real_ur_fixture_oracle_is_independent_of_new_implementation():
    values = sources()
    trees = ET.fromstring(values['behavior_tree']).findall('BehaviorTree')
    calls = [node for tree in trees for node in tree.find('Sequence')]
    assert len(calls) == 25
    assert sum(len(set(n.attrib) - {'ID', 'name'}) for n in calls) == 129
    manifest = json.loads((FIXTURES / 'provenance.json').read_text())
    assert hashlib.sha256((FIXTURES / 'ur_selected_tbox.json').read_bytes()).hexdigest() == manifest['fixture_sha256']


def test_evidence_is_deterministic_role_bound_and_complete():
    values = sources()
    before = deepcopy(values)
    first = bundle(values)
    assert first == bundle(dict(reversed(list(values.items()))))
    assert values == before
    assert {d['role'] for d in first['documents']} == set(values)
    documents = {d['id']: d for d in first['documents']}
    assert len(documents) == 2
    for doc in documents.values():
        assert doc['sha256'] == hashlib.sha256(values[doc['role']]).hexdigest()
    locations = [(loc['document_id'], loc['path']) for row in first['evidence'] for loc in row['locations']]
    assert len(locations) == len(set(locations))
    for doc in documents.values():
        root = ET.fromstring(values[doc['role']])
        # Comments/PIs may be retained too; every actual element must be covered.
        actual = [p for d, p in locations if d == doc['id'] and 'comment()' not in p
                  and 'processing-instruction()' not in p]
        assert len(actual) == sum(1 for _ in root.iter())


def test_repeated_identical_facts_keep_both_occurrence_paths():
    values = {'behavior_tree': b'<root><BehaviorTree ID="Main"><Sequence><Read/><Read/></Sequence></BehaviorTree></root>',
              'node_models': b'<root><TreeNodesModel><Action ID="Read"/></TreeNodesModel></root>'}
    evidence = bundle(values)['evidence']
    locations = [loc['path'] for row in evidence if row['facts']['tag'] == 'Read' for loc in row['locations']]
    assert len(locations) == 2 and len(set(locations)) == 2
    assert any(p.endswith('/Read[1]') for p in locations)
    assert any(p.endswith('/Read[2]') for p in locations)


@pytest.mark.parametrize('role', ['behavior_tree', 'node_models'])
@pytest.mark.parametrize('bad', [b'<broken>', b'<!DOCTYPE root><root/>',
    '<!DOCTYPE root [<!ENTITY e "x">]><root>&e;</root>'.encode('utf-16')])
def test_modeling_parser_enforces_common_xml_safety(role, bad):
    module = api('evidence')
    values = sources()
    values[role] = bad
    with pytest.raises(ValueError):
        module.ingest_sources(values)


def test_bundled_vocabulary_has_pinned_identity_and_separate_term_kinds(monkeypatch):
    monkeypatch.setenv('TBOX_LLM_KEY', 'unused-source-project-secret')
    vocab = api('vocabulary').load_vocabulary()
    assert vocab['sha256'] == 'f90d2c16c92372ff904e8200641411b6873d415f105441fd5fc1915e1400e9dc'
    assert {'AtomicSkill', 'CompositeSkill', 'SkillInvocation'} <= {r['id'] for r in vocab['classes']}
    assert 'hasInput' in {r['id'] for r in vocab['object_properties']}
    assert 'stepIndex' in {r['id'] for r in vocab['data_properties']}
    assert 'unused-source-project-secret' not in json.dumps(vocab)
    again = api('vocabulary').load_vocabulary()
    vocab['classes'].clear()
    assert again['classes'], 'Callers must not mutate a shared vocabulary cache'


def test_designs_frozen_on_the_first_vocabulary_bytes_still_count_as_the_same_vocabulary():
    """首版词表只改了 source 字段里的本机路径(换成占位符),术语未变:按首版字节冻结的设计仍按同一词表处理。"""
    module = api('vocabulary')
    assert module.same_vocabulary('eef9314fb13144a35efd659f6fd863dcc89cebf1ae0e54c3777bcdcf8e2a06aa')
    assert module.same_vocabulary(module.load_vocabulary()['sha256'])
    assert not module.same_vocabulary('0' * 64)


def test_frozen_vocabulary_identity_survives_both_source_metadata_aliases():
    module = api('vocabulary')
    for digest in ('f90d2c16c92372ff904e8200641411b6873d415f105441fd5fc1915e1400e9dc',
                   '866cfc32f78bcc37a31aefdeb32da68e6f31c5b7f05e08a368aed05cc1a96996'):
        assert module.same_vocabulary(digest), 'Source-only metadata changes must retain frozen vocabulary identity'
    assert not module.same_vocabulary('f' * 64)
