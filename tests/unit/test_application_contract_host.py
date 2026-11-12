"""T04 RED: real host wiring, read paths and existing generation guard — DR-071."""
from copy import deepcopy
import json
import sqlite3

import pytest

from application_contracts import ContractError
from domain_runs import RunStore
from domain_runs.robotics_task_service import TaskService
from ontology_assets.robotics_design import DesignAssets
from tests.application_contract_support import ENTRYPOINT, no_network
from tests.application_contract_integration_support import error, ok, post, request_body, service_api
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_design_support import Harness, restore_v5
from tests.robotics_governance_support import TASK, actual_build
from tests.robotics_task_support import fixture, start

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def built(root, method):
    if method == 'llm':
        state = Harness(root).candidate()
        return state['output_asset_ref']
    _, run = actual_build(root)
    return run['output_asset_ref']


@pytest.mark.parametrize('method', ['offline', 'llm'])
@pytest.mark.parametrize('view', ['catalog', 'detail', 'graph', 'compatibility'])
def test_build_and_browse_never_evaluate_application_contract(tmp_path, monkeypatch, method, view):
    api = service_api()
    def forbidden(*args, **kwargs):
        pytest.fail('Build/browse must not invoke application checks or the legacy consumer compatibility probe')
    monkeypatch.setattr(api.CheckService, 'evaluate', forbidden)
    monkeypatch.setattr(DesignAssets, 'compatibility', forbidden)
    with http_app(tmp_path) as (_, client, _, root):
        ref = built(root, method)
        base = f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}"
        path = {'catalog': '/api/ontology-assets?domain=robotics', 'detail': base,
                'graph': base + '/views/relations', 'compatibility': base + '/compatibility'}[view]
        before, files = db_rows(root), blob_bytes(root)
        response = ok(client.get(path, base_url=BASE_URL))
        if view == 'catalog':
            response = next(r for r in response['items'] if r['ontology_id'] == ref['ontology_id'])
        summary = response if view == 'compatibility' else response['application_compatibility']
        assert summary['status'] == 'not_assessed'
        assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('method,preparation', [('offline', 'available'), ('llm', 'blocked')])
def test_real_host_checks_only_on_post_and_projects_history_without_recheck(tmp_path, monkeypatch, method, preparation):
    with http_app(tmp_path) as (_, client, _, root):
        ref = built(root, method)
        before, files = db_rows(root), blob_bytes(root)
        created = ok(post(client, request_body(ref)), 201)
        report = created['report']
        assert report['preparation_status'] == preparation
        assert report['execution_status'] == 'not_evaluated' and report['task_ref'] is None
        if method == 'llm':
            assert any(i['code'] == 'execution.consumer_incompatible' for i in report['issues'])
            assert not any(i['code'] == 'schema.missing_role' for i in report['issues'])
        after = db_rows(root)
        assert all(after[t] == rows for t, rows in before.items() if not t.startswith('application_'))
        assert blob_bytes(root) == files
        def forbidden(*args, **kwargs):
            pytest.fail('Historical report projection must not re-evaluate application conditions')
        monkeypatch.setattr(service_api().CheckService, 'evaluate', forbidden)
        monkeypatch.setattr(DesignAssets, 'compatibility', forbidden)
        base = f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}"
        summary = ok(client.get(base + '/compatibility', base_url=BASE_URL))
        assert summary['check_id'] == report['check_id'] and summary['freshness'] == 'current'
        detail = ok(client.get(base, base_url=BASE_URL))
        assert detail['application_compatibility'] == summary
        assert db_rows(root) == after


def test_host_read_of_old_schema_does_not_migrate(tmp_path):
    root = tmp_path / 'assets'; restore_v5(root)
    before, files = db_rows(root), blob_bytes(root)
    with http_app(tmp_path, enabled=False) as (_, client, _, _):
        error(client.get('/api/application-checks/unknown', base_url=BASE_URL), 503, 'storage_unavailable')
    assert db_rows(root) == before and blob_bytes(root) == files


def prepared_task(root):
    _, version, _ = fixture(root, ready=True)
    service = TaskService(root, generation_policy='offline')
    state = start(service, version)
    # Synthetic server-side task setup: bypass only natural-language parsing, never generation/checking.
    state.update(task=deepcopy(TASK), status='ready', task_description='合成抓移放测试')
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        conn.execute('UPDATE robotics_task_sessions SET record=? WHERE session_id=?',
            (json.dumps(state), state['session_id']))
    return state


@pytest.mark.parametrize('code', ['application_not_ready', 'application_unavailable', 'storage_unavailable'])
def test_direct_existing_generate_route_must_call_current_application_guard(tmp_path, monkeypatch, code):
    api = service_api()
    calls = []
    def blocked(self, *, entrypoint, asset_refs, context, previous_report=None):
        calls.append((entrypoint, asset_refs, context, previous_report))
        raise ContractError(code, '合成阻塞条件')
    monkeypatch.setattr(api.CheckService, 'require_ready', blocked)
    with http_app(tmp_path) as (_, client, _, root):
        state = prepared_task(root)
        before = RunStore(root, read_only=True).list_runs()
        response = ok(post(client, {'expected_revision': state['revision'], 'request_id': 'generate'},
            f"/api/robotics/tasks/{state['session_id']}/generate"))
        assert response['status'] == 'error' and response['error']['code'] == code
        assert response['candidate'] is None and response['run_id'] is None
        assert len(calls) == 1 and calls[0][0] == ENTRYPOINT
        assert calls[0][1] == [{k: state[k] for k in ('ontology_id', 'version_id')}]
        assert calls[0][2]['task_ref'] == {'session_id': state['session_id']}
        assert RunStore(root, read_only=True).list_runs() == before


@pytest.mark.parametrize('forged', [{'ready': True}, {'check_id': 'old-ready-report'},
    {'application_id': 'data.deep_qa'}, {'generation_policy': 'offline'}])
def test_direct_generation_cannot_accept_client_ready_or_other_application_report(tmp_path, forged):
    with http_app(tmp_path) as (_, client, _, root):
        state = prepared_task(root)
        before, files = db_rows(root), blob_bytes(root)
        error(post(client, {'expected_revision': state['revision'], 'request_id': 'forged', **forged},
            f"/api/robotics/tasks/{state['session_id']}/generate"), 400, 'invalid_request')
        assert db_rows(root) == before and blob_bytes(root) == files


def test_ready_generation_still_uses_existing_compiler_and_fixed_snapshot(tmp_path, monkeypatch):
    api = service_api(); original = api.CheckService.require_ready; calls = []
    def observed(self, **kwargs):
        report = original(self, **kwargs)
        calls.append((deepcopy(kwargs), deepcopy(report)))
        return report
    monkeypatch.setattr(api.CheckService, 'require_ready', observed)
    with http_app(tmp_path) as (_, client, _, root):
        state = prepared_task(root)
        response = ok(post(client, {'expected_revision': state['revision'], 'request_id': 'generate'},
            f"/api/robotics/tasks/{state['session_id']}/generate"))
        from tests.robotics_task_support import assert_generated
        assert_generated(root, response)
        assert len(calls) == 1 and calls[0][1]['execution_status'] == 'ready'
        run = RunStore(root, read_only=True).get_run(response['run_id'])
        assert run['ontology_id'] == state['ontology_id'] and run['version_id'] == state['version_id']
        assert calls[0][1]['asset_refs'] == [{k: state[k] for k in ('ontology_id', 'version_id')}]
