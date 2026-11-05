"""IR-020 T02: user choices, not model guesses, determine grounding — DR-062 / DR-066."""
from copy import deepcopy
import importlib

import pytest
from rdflib import Graph, Literal, RDF, RDFS, URIRef

from tests.robotics_support import S


def api():
    return importlib.import_module('domains.robotics.task_dialogue')


def catalog():
    return [
        {'kind': 'Workpiece', 'id': 'urn:factory:part-a', 'label': '零件A', 'aliases': ['工件甲']},
        {'kind': 'Workpiece', 'id': 'urn:factory:part-b', 'label': '零件B', 'aliases': []},
        {'kind': 'Workstation', 'id': 'urn:factory:station-a', 'label': '工位A', 'aliases': []},
        {'kind': 'Workstation', 'id': 'urn:factory:station-b', 'label': '工位B', 'aliases': []},
        {'kind': 'Carrier', 'id': 'urn:factory:carrier-a', 'label': '载具A', 'aliases': []},
        {'kind': 'Carrier', 'id': 'urn:factory:carrier-b', 'label': '载具B', 'aliases': []},
        {'kind': 'PlacementSlot', 'id': 'urn:factory:slot-a', 'label': '槽位A', 'aliases': []},
    ]


def task(**changes):
    return {'operation': 'pick_place', 'part': '零件A', 'station': '工位A',
            'carrier': '载具A', 'slot': '槽位A', 'unsupported_requests': [], **changes}


def messages(*contents):
    return [{'role': 'user', 'content': content} for content in contents]


def test_catalog_exports_only_business_names_with_full_identity_without_graph_mutation():
    graph = Graph()
    first, second = URIRef('urn:factory:a#part'), URIRef('urn:factory:b#part')
    for entity in (first, second):
        graph.add((entity, RDF.type, S.Workpiece))
        graph.add((entity, RDFS.label, Literal('同名零件')))
    graph.add((first, S.alias, Literal('工件甲')))
    graph.add((S.HiddenPose, RDF.type, S.Pose))
    graph.add((S.HiddenPose, S.rawValue, Literal('secret-pose-values')))
    before = set(graph)
    exported = api().catalog_from_graph(graph)
    assert set(graph) == before
    assert {item['id'] for item in exported} == {str(first), str(second)}
    assert all(set(item) == {'kind', 'id', 'label', 'aliases'} for item in exported)
    assert all(item['kind'] == 'Workpiece' and item['label'] == '同名零件' for item in exported)
    assert next(item for item in exported if item['id'] == str(first))['aliases'] == ['工件甲']
    assert 'secret-pose-values' not in str(exported)


def test_explicit_user_choices_and_aliases_are_resolved_to_exact_identities():
    conversation = messages('把工件甲放到工位A的载具A槽位A')
    proposed, directory = task(), catalog()
    before = deepcopy((proposed, conversation, directory))
    request, issues = api().grounded_mentions(proposed, conversation, directory)
    assert request == {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-a',
                       'carrier': 'urn:factory:carrier-a', 'slot': 'urn:factory:slot-a'}
    assert issues == []
    assert (proposed, conversation, directory) == before


def test_model_guesses_and_assistant_candidates_do_not_count_as_user_choices():
    conversation = messages('生成抓移放任务') + [
        {'role': 'assistant', 'content': '候选：零件A、工位A、载具A、槽位A'}]
    # Even with one candidate of each type, only the user can select it.
    directory = [item for item in catalog() if item['id'].endswith('-a')]
    request, _ = api().grounded_mentions(task(), conversation, directory)
    assert request == dict.fromkeys(('part', 'station', 'carrier', 'slot'))


def test_missing_target_stays_missing_when_model_returns_full_task():
    request, _ = api().grounded_mentions(task(), messages('抓取零件A'), catalog())
    assert request == {'part': 'urn:factory:part-a', 'station': None, 'carrier': None, 'slot': None}


@pytest.mark.parametrize('reply', ['可能用零件A', '不要零件A', '撤销零件A', '零件A或零件B'])
def test_uncertain_negative_retracted_or_ambiguous_choice_requires_clarification(reply):
    conversation = messages('把零件A放到工位A的载具A槽位A', reply)
    request, issues = api().grounded_mentions(task(), conversation, catalog())
    assert request['part'] is None
    assert any(item['field'] == 'part' and item['code'] in ('uncertain_entity', 'ambiguous_entity')
               for item in issues)
    assert request['station'] is request['carrier'] is request['slot'] is None


@pytest.mark.parametrize('reply, expected', [
    ('改用零件B', {'part': 'urn:factory:part-b', 'station': None, 'carrier': None, 'slot': None}),
    ('改到工位B', {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-b',
                    'carrier': None, 'slot': None}),
    ('改用载具B', {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-a',
                   'carrier': 'urn:factory:carrier-b', 'slot': None}),
])
def test_changing_upstream_choice_invalidates_old_downstream_choices(reply, expected):
    request, _ = api().grounded_mentions(task(), messages('把零件A放到工位A的载具A槽位A', reply), catalog())
    assert request == expected


def test_later_explicit_answer_resolves_uncertainty_and_preserves_unmodified_choices():
    conversation = messages('把零件A放到工位A的载具A', '也许是槽位A', '选择槽位A')
    request, issues = api().grounded_mentions(task(), conversation, catalog())
    assert request == {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-a',
                       'carrier': 'urn:factory:carrier-a', 'slot': 'urn:factory:slot-a'}
    assert issues == []


def test_duplicate_labels_require_choice_and_full_iri_selection_disambiguates():
    directory = [{'kind': 'Workpiece', 'id': 'urn:factory:a#part', 'label': '零件A', 'aliases': []},
                 {'kind': 'Workpiece', 'id': 'urn:factory:b#part', 'label': '零件A', 'aliases': []}]
    request, issues = api().grounded_mentions(task(), messages('抓取零件A'), directory)
    assert request['part'] is None
    issue = next(item for item in issues if item['field'] == 'part')
    assert issue['code'] == 'ambiguous_entity'
    assert {item['id'] for item in issue['candidates']} == {item['id'] for item in directory}
    request, issues = api().grounded_mentions(task(part='urn:factory:b#part'),
        messages('抓取零件A', '选择 urn:factory:b#part'), directory)
    assert request['part'] == 'urn:factory:b#part'
    assert not any(item['field'] == 'part' for item in issues)


def test_unknown_explicit_name_is_not_silently_replaced_by_catalog_candidate():
    request, _ = api().grounded_mentions(task(part='零件Z'), messages('抓取零件Z'), catalog())
    assert request['part'] == '零件Z'
    assert request['station'] is request['carrier'] is request['slot'] is None


def test_confirmation_phrase_does_not_choose_missing_objects_or_create_knowledge():
    conversation = messages('把零件A放到工位A', '已确认适配，生成吧')
    proposed, directory = task(), catalog()
    before = deepcopy((proposed, conversation, directory))
    request, _ = api().grounded_mentions(proposed, conversation, directory)
    assert request == {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-a',
                       'carrier': None, 'slot': None}
    assert (proposed, conversation, directory) == before
