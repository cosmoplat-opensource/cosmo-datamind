"""代码评审问题回归：控制字符、撤销旧证据、源校验定位、对话保留、标识冲突、错误类型。"""
from copy import deepcopy
import tempfile
from pathlib import Path

import pytest
from rdflib import RDF

from domains.robotics import authoring, compiler, generation, grounding, task_dialogue, task_knowledge
from domains.robotics.source_validation import SourceValidationError
from domains.robotics.validator import _baseline_errors
from domains.robotics.xml_contract import _literal_problem
from domains.robotics.modeling import _tbox_validation, tbox as tbox_module
from domains.robotics.modeling.common import ModelingError
from domains.robotics.modeling import evidence as modeling_evidence
from tests.robotics_generation_support import context, task
from tests.robotics_modeling_support import (
    NS, ScriptedRoute, api as modeling_api, bundle, classes, confirmation, ur_schema, vocabulary)
from tests.robotics_support import MODELS, SOURCE, S, build, config, prepared
from tests.robotics_governance_support import TASK
from tests.unit.test_robotics_task_completion import ready_graph

CONTROL = ['ac\x01', 'ac\x08', 'ac\x0b', 'ac\x0c', 'ac\x1f']


# H1：控制字符不得进入 RDF/XML
@pytest.mark.parametrize('value', CONTROL)
def test_authoring_rejects_xml_control_characters(value):
    graph = build()
    with pytest.raises(ValueError):
        authoring.apply_operations(graph, [{'op': 'set_field', 'iri': str(S.Perception_Current),
                                            'field': 'objectName', 'value': value, 'basis': '测试'}])
    with pytest.raises(ValueError):
        authoring.validate_value('PerceptionProfile', 'objectName', value)


@pytest.mark.parametrize('value', CONTROL)
def test_task_knowledge_parameter_answer_rejects_control_characters(value):
    graph = ready_graph()
    graph.remove((S.Perception_Current, S.objectName, None))
    projection = task_knowledge.inspect_gaps(graph, deepcopy(TASK), mode='real')
    gap = next(item for item in projection['items'] if item['field'] == 'objectName')
    with pytest.raises(ValueError):
        task_knowledge.apply_answers(graph, deepcopy(TASK), [{'gap_id': gap['id'], 'value': value}], mode='real')


@pytest.mark.parametrize('value', CONTROL)
def test_compiler_instantiate_rejects_control_characters(value):
    template, models, paths = prepared()
    values = config()
    values['Perception']['object_name'] = value
    with pytest.raises(ValueError):
        compiler.instantiate(template, values, baseline=template, models_root=models, config_paths=paths)


@pytest.mark.parametrize('value', CONTROL)
def test_literal_problem_and_baseline_reject_control_characters(value):
    assert _literal_problem('PoseEstimate', 'object_name', value, 'std::string') is not None
    template, _, paths = prepared()
    candidate = deepcopy(template)
    skill, fields = next(iter(paths.items()))
    name, (tree_id, index, port) = next(iter(fields.items()))
    node = candidate.find(f"BehaviorTree[@ID='{tree_id}']")[0][index]
    node.set(port, value)
    errors = _baseline_errors(candidate, template, paths)
    assert any(item['code'] == 'configuration_literal' for item in errors)


# M1：被撤销的旧式证据不能让严格生成写出候选
def test_revoked_legacy_evidence_blocks_strict_generation():
    loaded = context('demo')
    graph = loaded['graph']
    items = list(graph.subjects(RDF.type, S.ReadinessEvidence))
    assert items
    loaded['evidence_revocations'] = [{'evidence_iri': str(item), 'kind': 'revoked'} for item in items]
    with tempfile.TemporaryDirectory() as directory:
        result = generation.generate_task(loaded, task(), mode='demo', output_dir=directory)
        assert result['candidate_written'] is False
        assert not list(Path(directory).rglob('*.xml'))
    kind, implementation = 'grasp_compatibility', S.grasp_Subtree_Impl
    part_type = graph.value(S.Part_A_001, S.hasPartType)
    assert grounding.compatibility_confirmed(graph, kind, implementation, part_type, None, mode='demo')
    assert not grounding.compatibility_confirmed(graph, kind, implementation, part_type, None, mode='demo',
                                                 revocations=loaded['evidence_revocations'])


# M2：源 XML 的输出绑定与标识须在导入前给出定位错误
def _sources(tmp_path, source_bytes=None, model_bytes=None):
    source, models = tmp_path / 'bt.xml', tmp_path / 'models.xml'
    source.write_bytes(source_bytes if source_bytes is not None else SOURCE.read_bytes())
    models.write_bytes(model_bytes if model_bytes is not None else MODELS.read_bytes())
    return source, models


def test_output_port_bound_to_literal_is_a_located_source_error(tmp_path):
    raw = SOURCE.read_bytes()
    changed = raw.replace(b'pose="{pose}"', b'pose="literal_pose"', 1)
    assert changed != raw
    source, models = _sources(tmp_path, changed)
    from domains.robotics.importer import build_graph
    with pytest.raises(SourceValidationError) as failure:
        build_graph(source, models)
    location = failure.value.issues[0]['location']
    assert failure.value.issues[0]['code'] == 'invalid_binding'
    assert location['port'] == 'pose' and location['tree_id'] == 'Perception'


@pytest.mark.parametrize('old,new,field', [
    (b'<Action ID="ErrorToFSM">', b'<Action ID="Error To FSM">', 'model_id'),
    (b'<input_port name="ercode" type="std::string"/>', b'<input_port name="er&quot;code" type="std::string"/>',
     'port'),
])
def test_invalid_model_identifiers_are_located_source_errors(tmp_path, old, new, field):
    raw = MODELS.read_bytes()
    assert old in raw
    source, models = _sources(tmp_path, model_bytes=raw.replace(old, new))
    from domains.robotics.importer import build_graph
    with pytest.raises(SourceValidationError) as failure:
        build_graph(source, models)
    issue = failure.value.issues[0]
    assert issue['code'] == 'invalid_identifier' and issue['source_role'] == 'node_models'
    assert field in issue['location']


def test_set_blackboard_output_key_must_be_an_identifier(tmp_path):
    raw = SOURCE.read_bytes()
    changed = raw.replace(b'output_key="home"', b'output_key="ho me"')
    assert changed != raw
    source, models = _sources(tmp_path, changed)
    from domains.robotics.importer import build_graph
    with pytest.raises(SourceValidationError) as failure:
        build_graph(source, models)
    issue = failure.value.issues[0]
    assert issue['code'] == 'invalid_identifier' and issue['location']['port'] == 'output_key'


# L3：节点模型中自行声明的 SetBlackboard 与内置契约不一致时报冲突
def test_declared_set_blackboard_conflict_is_reported(tmp_path):
    raw = MODELS.read_bytes()
    declared = (b'<Action ID="SetBlackboard"><input_port name="value" type="int"/>'
                b'<input_port name="output_key" type="std::string"/></Action></TreeNodesModel>')
    source, models = _sources(tmp_path, model_bytes=raw.replace(b'</TreeNodesModel>', declared))
    from domains.robotics.importer import build_graph
    with pytest.raises(SourceValidationError) as failure:
        build_graph(source, models)
    issue = failure.value.issues[0]
    assert issue['code'] == 'model_conflict' and issue['location']['model_id'] == 'SetBlackboard'


def test_declared_set_blackboard_matching_builtin_is_accepted(tmp_path):
    raw = MODELS.read_bytes()
    declared = (b'<Action ID="SetBlackboard"><input_port name="value" type="Any"/>'
                b'<input_port name="output_key" type="std::string"/></Action></TreeNodesModel>')
    source, models = _sources(tmp_path, model_bytes=raw.replace(b'</TreeNodesModel>', declared))
    from domains.robotics.importer import build_graph
    assert (S.SetBlackboard_BT_Impl, RDF.type, S.SkillImplementation) in build_graph(source, models)


# M3：追问未解析到对象时不得静默清空先前选择
def test_follow_up_without_resolved_entity_keeps_earlier_choice():
    catalog = [
        {'kind': 'Workpiece', 'id': 'urn:factory:part-a', 'label': '零件A', 'aliases': []},
        {'kind': 'Workstation', 'id': 'urn:factory:station-a', 'label': '工位A', 'aliases': []},
        {'kind': 'Workstation', 'id': 'urn:factory:station-b', 'label': '工位B', 'aliases': []},
        {'kind': 'Carrier', 'id': 'urn:factory:carrier-a', 'label': '载具A', 'aliases': []},
        {'kind': 'PlacementSlot', 'id': 'urn:factory:slot-a', 'label': '槽位A', 'aliases': []},
    ]
    proposed = {'operation': 'pick_place', 'part': '零件A', 'station': '工位A', 'carrier': '载具A',
                'slot': '槽位A', 'unsupported_requests': []}
    conversation = [{'role': 'user', 'content': '把零件A放到工位A的载具A槽位A'},
                    {'role': 'user', 'content': '工位就用刚才那个'}]
    selected, issues = task_dialogue.grounded_mentions(proposed, conversation, catalog)
    assert selected == {'part': 'urn:factory:part-a', 'station': 'urn:factory:station-a',
                        'carrier': 'urn:factory:carrier-a', 'slot': 'urn:factory:slot-a'}
    assert issues == []


# M4：不同原始名拼接后撞名时报错，不得静默合并
def test_identifier_collision_is_rejected():
    values = {'behavior_tree': b'<root><BehaviorTree ID="Main"><Sequence>'
              b'<Pick obj_pose="1"/><Pick_obj pose="2"/>'
              b'</Sequence></BehaviorTree></root>',
              'node_models': b'<root><TreeNodesModel>'
              b'<Action ID="Pick"><input_port name="obj_pose" type="int"/></Action>'
              b'<Action ID="Pick_obj"><input_port name="pose" type="int"/></Action>'
              b'</TreeNodesModel></root>'}
    evidence = bundle(values)
    with pytest.raises(ModelingError) as failure:
        modeling_api('abox').extract(confirmation(ur_schema(), evidence), evidence)
    assert failure.value.code == 'unsupported_source' and failure.value.issues


# L2：语义拒绝与来源结构错误须带错误码
def test_semantic_rejection_in_revise_is_modeling_error():
    state = tbox_module.new_design(bundle(), vocabulary(), namespace=NS)
    bad = classes()
    bad['classes'][0]['id'] = 'NotInVocabulary'
    with pytest.raises(ModelingError) as failure:
        tbox_module.revise(state, '合成用途', ScriptedRoute(bad), session_id='design-test', timeout=7)
    assert failure.value.code


def test_source_digests_structure_error_is_modeling_error():
    evidence = bundle()
    damaged = deepcopy(evidence)
    damaged['evidence'][1]['locations'] = []
    with pytest.raises(ModelingError) as failure:
        modeling_evidence.source_digests(damaged)
    assert failure.value.code == 'invalid_source'


# L4：定义域为 owl:Thing 的数据属性在响应契约与本地校验中一致
def test_data_property_with_owl_thing_domain_is_consistent():
    from jsonschema import Draft202012Validator
    terminology = vocabulary()
    terminology['data_properties'][0]['domain_ids'] = ['owl:Thing']
    frozen = classes()
    proposal = {'reply': '合成属性提议。', 'object_properties': [], 'clarifications': [],
                'data_properties': [{'id': 'stepIndex', 'label': '序号', 'definition': '步骤序号',
                                     'domain_ids': ['owl:Thing'], 'class_ids': ['Atomic'],
                                     'datatype': 'xsd:integer', 'source_refs': ['M0001'], 'cq_refs': ['CQ1']}]}
    schema = _tbox_validation.response_schema(terminology, 'properties', frozen['classes'])
    assert next(Draft202012Validator(schema).iter_errors(proposal), None) is None
    checked = _tbox_validation.validate_properties(proposal, set(), {'M0001'}, terminology,
                                                    frozen['classes'], frozen['competency_questions'])
    assert checked['data_properties'][0]['domain_ids'] == ['owl:Thing']


# 端口 IRI 由「节点 ID__端口名」拼成;ID 与端口名可以含下划线,两个不同的端口可能拼出同一个 IRI
def test_port_identifiers_that_collide_after_joining_are_located_source_errors(tmp_path):
    raw = MODELS.read_bytes()
    extra = (b'<Action ID="Grip_"><input_port name="b" type="double"/></Action>'
             b'<Action ID="Grip"><input_port name="_b" type="double"/></Action></TreeNodesModel>')
    assert raw.count(b'</TreeNodesModel>') == 1
    source, models = _sources(tmp_path, model_bytes=raw.replace(b'</TreeNodesModel>', extra))
    from domains.robotics.importer import build_graph
    with pytest.raises(SourceValidationError) as failure:
        build_graph(source, models)
    issue = failure.value.issues[0]
    assert issue['code'] == 'identifier_collision' and issue['source_role'] == 'node_models'
    assert {issue['location']['model_id'], issue['location']['port']} & {'Grip', '_b', 'Grip_', 'b'}


# 保存版本前把序列化后的 OWL 再解析一次:漏过输入校验的字符也不能写出读不回来的版本
def test_saved_owl_bytes_are_parsed_back_before_a_version_is_written():
    from rdflib import Graph, Literal, URIRef
    from domain_runs.robotics_task_service import TaskError, checked_owl_bytes
    g = Graph()
    g.add((URIRef('urn:x:a'), URIRef('urn:x:name'), Literal('ok')))
    assert b'urn:x:a' in checked_owl_bytes(g)
    g.add((URIRef('urn:x:b'), URIRef('urn:x:name'), Literal('ab\x01c')))
    with pytest.raises(TaskError) as failure:
        checked_owl_bytes(g)
    assert failure.value.code == 'invalid_answer'
