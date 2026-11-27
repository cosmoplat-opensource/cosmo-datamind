"""Saved facts and actual generation failures remain distinct — DR-174."""
import pytest
from rdflib import RDF, RDFS, Literal
from rdflib.compare import isomorphic

from domain_runs.robotics_task_service import TaskService, _validation_feedback
from domains.robotics import evidence, consumer
from domains.robotics.graph import S
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.robotics_governance_support import NOW
from tests.robotics_task_support import (fixture, model, start, message, complete,
                                         synthetic_answers, graph_of, assert_no_result, assert_generated)


@pytest.mark.parametrize('phase', ['save', 'initial'])
@pytest.mark.parametrize('problem', ['missing', 'invalid'])
def test_unused_configuration_is_preserved_and_reported_only_by_full_validation(tmp_path, monkeypatch, problem, phase):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    def mutate(graph):
        graph.add((S.OtherProfile, RDF.type, S.PerceptionProfile))
        graph.add((S.OtherProfile, RDFS.label, Literal('备用感知配置')))
        if problem == 'invalid':
            for field in ('objectName', 'modelName', 'threshold', 'exposureTime', 'hasCaptureJoints', 'confirmationStatus'):
                graph.add((S.OtherProfile, S[field], graph.value(S.Perception_Current, S[field])))
            graph.add((S.OtherProfile, S.threshold, Literal('0.123')))
        if phase == 'save':
            graph.remove((S.Perception_Current, S.exposureTime, None))
    root = tmp_path / 'assets'
    assets, version, _ = fixture(root, ready=True, mutate=mutate)
    service = TaskService(root, model_client=model()[0], generation_policy='offline')
    current = message(service, start(service, version))
    for index in range(4):
        if current['status'] == 'generated':
            break
        assert current['status'] == 'needs_information' and current['gaps']
        answers = synthetic_answers(current)
        for answer in answers:
            if 'value' in answer:
                answer['value'] = '155'
        current = complete(service, current, answers, request_id='complete-'+str(index))
    from tests.robotics_support import config
    values = config()
    if phase == 'save':
        values['Perception']['exposure_time'] = '155'
    assert_generated(root, current, values=values)
    assert current['error'] is None and current['issues'] == []
    assert current['result']['validation']['validation_scope']['kind'] == 'task_configuration'
    before_graph = graph_of(root, current)
    before_payload = assets.read_payload(current['ontology_id'], current['version_id'])
    with pytest.raises(consumer.GraphValidationError) as error:
        assets.load_robotics_application(current['ontology_id'], current['version_id'])
    problems = _validation_feedback(error.value)
    text = ''.join(p['message'] for p in problems)
    expected = ('识别对象名', '识别模型名', '识别阈值', '曝光时间', '拍照关节配置') if problem == 'missing' else ('需要调整', '识别阈值')
    for term in ('备用感知配置', *expected):
        assert term in text
    assert 'SHACL' not in text and 'MinCount' not in text and 'http:' not in text
    assert all(p['code'] == 'ontology_invalid_configuration' for p in problems)
    assert isomorphic(graph_of(root, current), before_graph)
    assert assets.read_payload(current['ontology_id'], current['version_id']) == before_payload
    if phase == 'save':
        assert current['version_id'] != version['version_id']
        assert str(graph_of(root, current).value(S.Perception_Current, S.exposureTime)) == '155'
    else:
        assert current['version_id'] == version['version_id']
    before = db_rows(root), blob_bytes(root)
    restored = service.get(current['session_id'])
    assert restored['error'] == current['error'] and restored['issues'] == current['issues']
    assert (db_rows(root), blob_bytes(root)) == before
    assert assets.read_payload(version['ontology_id'], version['version_id'])


@pytest.mark.parametrize('failure,expected', [
    (OSError('synthetic-private-detail'), '结果暂时无法保存'),
    (ValueError('synthetic-private-detail'), '检查本体内容'),
])
def test_saved_generation_failures_preserve_facts_without_leaking_internal_exception(tmp_path, monkeypatch, failure, expected):
    assets, version, _ = fixture(tmp_path / 'assets')
    service = TaskService(tmp_path / 'assets', model_client=model()[0], generation_policy='offline')
    current = message(service, start(service, version))
    assert current['status'] == 'needs_information'
    def fail(state):
        raise failure
    monkeypatch.setattr(service, '_evaluate', fail)
    saved = complete(service, current, synthetic_answers(current))
    assert saved['status'] == 'error' and saved['version_id'] != current['version_id']
    assert saved['error']['code'] == 'generation_error'
    assert expected in saved['error']['message'] and 'synthetic-private-detail' not in saved['error']['message']
    before = db_rows(service.root), blob_bytes(service.root)
    restored = service.get(saved['session_id'])
    for key in ('status', 'error', 'session_id', 'version_id', 'candidate'):
        assert restored[key] == saved[key]
    assert (db_rows(service.root), blob_bytes(service.root)) == before
    assert assets.get_version(saved['ontology_id'], saved['version_id'])
    assert_no_result(saved)
