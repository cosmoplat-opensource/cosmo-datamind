"""Named schema gaps and contextual, user-confirmed repair advice — DR-072."""
from urllib.parse import parse_qs

import pytest

from domains.robotics.application_adaptation import resolve_roles
from tests.robotics_modeling_support import ur_schema
from tests.robotics_adaptation_support import role_map
from tests.robotics_application_integration_support import candidate, check_body
from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import db_rows, blob_bytes


@pytest.mark.parametrize('kind,role,label', [
    ('classes', 'ImplementationConfiguration', '固定实现配置'),
    ('object_properties', 'hasFixedConfiguration', '固定实现配置'),
    ('data_properties', 'scope', '数据作用域'),
])
def test_missing_role_identifies_kind_name_and_required_semantics(kind, role, label):
    schema = ur_schema()
    mapping = role_map(schema)
    mapping['bindings'] = [b for b in mapping['bindings'] if (b['kind'], b['role']) != (kind, role)]
    result = resolve_roles(tbox=schema, role_map=mapping)
    gap = next(i for i in result['issues'] if i.get('role') == role)
    assert role in gap['message'] and label in gap['message']
    assert {'classes': '类别', 'object_properties': '关系', 'data_properties': '数据属性'}[kind] in gap['message']
    assert gap['expected']['kind'] == kind and gap['expected']['role'] == role
    assert gap['expected']['label'] == label
    if kind == 'object_properties':
        assert gap['expected']['domain_ids'] == ['SkillImplementation']
        assert gap['expected']['range_ids'] == ['ImplementationConfiguration']
    if kind == 'data_properties':
        assert gap['expected']['datatype'] == 'xsd:string'


def test_schema_report_preserves_named_gaps_and_repair_carries_report_identity(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h, original, ref = candidate(root, missing_role='ParameterBinding')
        response = client.post('/api/application-checks', json=check_body(ref), base_url=BASE_URL)
        assert response.status_code == 201, response.json
        report = response.json['report']
        gaps = [i for i in report['issues'] if i['code'] == 'schema.missing_role']
        assert any(i['expected']['kind'] == 'classes' and i['expected']['role'] == 'ParameterBinding' for i in gaps)
        assert any(i['expected']['kind'] == 'object_properties' and i['expected']['role'] == 'hasBinding' for i in gaps)
        assert all(i['expected']['role'] in i['message'] for i in gaps)
        assert all(i['observed']['kind'] == i['expected']['kind'] for i in gaps)
        gap = gaps[0]
        action = next(a for a in gap['repair_actions'] if a['action_id'] == 'robotics.revise_tbox')
        before, files = db_rows(root), blob_bytes(root)
        reply = client.post('/api/application-checks/' + report['check_id'] + '/repairs',
            json={'issue_id': gap['issue_id'], 'action_id': action['action_id'], 'input': {},
                  'expected_revision': action['target']['expected_revision'], 'request_id': 'repair-guidance'},
            base_url=BASE_URL)
        assert reply.status_code == 200, reply.json
        query = parse_qs(reply.json['href'].split('?', 1)[1])
        assert query['session'] == [original['session_id']]
        assert query['check'] == [report['check_id']]
        assert h.get(original) == original and len(h.runtime.calls) == 2
        after = db_rows(root)
        for table in ('assets', 'versions', 'runs', 'design_sessions', 'design_revisions'):
            assert after[table] == before[table]
        assert blob_bytes(root) == files
