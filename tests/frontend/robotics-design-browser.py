"""Real Chromium + Gunicorn + persisted designs; only model HTTP scripted — DR-152 / T08 RED."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from playwright.sync_api import expect, sync_playwright

from domain_runs import RunStore
from domain_runs.robotics_design_service import DesignService
from ontology_assets.robotics_design import DesignAssets
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_browser_support import choose_robotics, fill_robotics, submit_from_ui, wait_for, wait_run_ui
from tests.robotics_design_browser_support import DesignSite, model_reply
from tests.robotics_design_support import Harness, proposals
from tests.robotics_task_support import model_server
from tests.robotics_seed_support import SeedCache
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.browser_runtime import browser_executable  # noqa: E402


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as pw:
        executable = browser_executable()
        instance = pw.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def live(tmp_path, request, browser, prepared_seeds):
    options = dict(getattr(request, 'param', {}))
    responses = options.pop('responses', [model_reply(p) for p in proposals()])
    offline = options.pop('offline', False)
    output = Path(os.environ.get('DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT',
        str(ROOT / '.check-results/robotics-design-browser'))) / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    with model_server(*responses) as (url, calls):
        site = DesignSite(tmp_path / 'site', output, model_url=None if offline else url,
                          prepare_cache=prepared_seeds, **options)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page(); page.set_default_timeout(3500); page.set_default_navigation_timeout(15000)
        site.observe(page)
        try:
            yield site, page, calls
        finally:
            try:
                page.screenshot(path=str(output / 'final.png'), full_page=True)
            except Exception as exc:
                site.evidence['screenshot_error'] = str(exc)
            site.evidence['model_requests'] = [{'path': c['path'], 'payload': c['payload']} for c in calls]
            context.close(); site.close()


@pytest.fixture(scope='module')
def prepared_seeds():
    # Only preconditions are cloned; all UI actions, HTTP calls and validations stay real.
    return SeedCache()


def done(site):
    assert not site.evidence['page_errors'], site.evidence['page_errors']
    site.evidence['assertions_completed'] = True


def session(site, sid):
    return site.get('/api/robotics/design-sessions/' + sid)


def wait_design(page, sid, stage, status):
    panel = page.get_by_test_id('robotics-design')
    expect(panel).to_have_attribute('data-session-id', sid)
    expect(panel).to_have_attribute('data-stage', stage)
    expect(panel).to_have_attribute('data-status', status, timeout=20000)
    return panel


def wait_completed_design(page, site, sid):
    """DR-166: completion leaves the editor; reopening remains a read-only history entry."""
    expect(page).to_have_url(re.compile(r'#robotics-view\?.*view=interactive-ontology-graph'), timeout=25000)
    ref = session(site, sid)['output_asset_ref']
    assert ref['ontology_id'] in page.url and ref['version_id'] in page.url
    expect(page.get_by_test_id('interactive-ontology-graph-panel')).to_have_attribute('data-kind', 'version')
    page.goto(site.url + '/#build?recipe=robotics&session=' + sid)
    return wait_design(page, sid, 'abox', 'completed')


def click_operation(page, name, suffix):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(suffix)) as pending:
        page.get_by_role('button', name=name, exact=True).click()
    assert pending.value.status in (200, 202), pending.value.text()
    return pending.value.json()


def ui_admit(page, site, text='整理技能调用与参数'):
    choose_robotics(page, site); fill_robotics(page)
    page.get_by_label('建模要求', exact=True).fill(text)
    return click_operation(page, '构建机器人本体', '/api/robotics/builds')


def test_real_browser_server_and_local_model_boundary_are_available(live):
    site, page, calls = live
    page.goto(site.url + '/#build')
    expect(page.locator('#bc_q')).to_be_visible()
    caps = site.get('/api/robotics/capabilities')
    assert caps['enabled'] and caps['building_method'] == 'llm_tbox_abox'
    assert caps['model']['model'] == 'ir024-test-model' and calls == []
    assert RunStore(site.root, read_only=True).list_runs() == []
    done(site)


def test_local_model_fixture_works_with_real_platform_runtime_and_design_core(tmp_path, monkeypatch):
    from openai_runtime import OpenAICompatRuntime
    from domain_runs.robotics_build_routing import resolve_route
    from tests.robotics_dual_build_support import RuntimeRegistry, model_config
    from tests.robotics_design_support import inputs
    with model_server(model_reply(proposals()[0])) as (url, calls):
        monkeypatch.setenv('DATAMIND_LLM_BASE', url)
        monkeypatch.setenv('DATAMIND_LLM_KEY', 'ir024-local-only')
        monkeypatch.setenv('DATAMIND_LLM_MODEL', 'ir024-test-model')
        runtime = OpenAICompatRuntime()
        def route():
            return resolve_route(config=model_config(base=url, key='ir024-local-only'), environ={},
                registry=RuntimeRegistry(openai=runtime))
        service = DesignService(tmp_path / 'fixture-control', route_resolver=route)
        state = service.submit(**inputs(description='整理技能调用与参数'))
        assert service.claim_next()['run_id'] == state['run_id']
        result = service.execute(state['run_id'])
        assert result['status'] == 'completed'
        assert service.get(state['session_id'])['revision'] == 1 and len(calls) == 1
        assert calls[0]['path'] == '/v1/chat/completions'


@pytest.mark.parametrize('live', [{'offline': True}], indirect=True)
def test_same_build_entry_offline_still_builds_real_old_candidate(live):
    site, page, calls = live
    choose_robotics(page, site); fill_robotics(page)
    rid = submit_from_ui(page, site); wait_run_ui(page, rid, 'completed')
    row = RunStore(site.root, read_only=True).get_run(rid)
    assert row['operation'] == 'build_ontology' and row['result']['status'] == 'built'
    assert row['components']['model']['invoked'] is False and calls == []
    done(site)


@pytest.mark.parametrize('live', [{'offline': True}], indirect=True)
def test_offline_page_explains_description_not_used_and_has_no_mode_switch(live):
    site, page, calls = live
    choose_robotics(page, site)
    expect(page.locator('#rb_panel')).to_contain_text('离线')
    expect(page.locator('#rb_panel')).to_contain_text(re.compile('建模要求.*不参与'))
    expect(page.get_by_role('combobox', name='构建方式', exact=True)).to_have_count(0)
    assert calls == []
    done(site)


@pytest.mark.parametrize('live', [{'incomplete': True}], indirect=True)
def test_configuration_error_blocks_upload_without_offline_fallback(live):
    site, page, calls = live
    choose_robotics(page, site); fill_robotics(page)
    expect(page.get_by_role('button', name='构建机器人本体', exact=True)).to_be_disabled()
    expect(page.locator('#rb_panel')).to_contain_text(re.compile('配置.*(不完整|错误|缺少)'))
    assert site.design_posts() == [] and calls == [] and RunStore(site.root, read_only=True).list_runs() == []
    done(site)


def test_model_ui_stage_confirmations_final_downloads_and_history(live):
    site, page, calls = live
    admitted = ui_admit(page, site); sid = admitted['session_id']
    panel = wait_design(page, sid, 'classes', 'awaiting_confirmation')
    first = session(site, sid); assert first['revision'] == 1 and len(calls) == 1
    assert first['result']['classes'][0]['label'] in panel.inner_text()
    before_posts = len(site.design_posts()); page.reload()
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    assert len(site.design_posts()) == before_posts and len(calls) == 1
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input'); assert len(calls) == 1
    page.get_by_label('建模要求', exact=True).fill('补充关系与参数属性')
    click_operation(page, '发送建模要求', '/messages')
    wait_design(page, sid, 'properties', 'awaiting_confirmation'); assert len(calls) == 2
    schema = session(site, sid)
    old_json = DesignService(site.root, read_only=True).export_revision(sid, schema['revision'], format='json')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    wait_completed_design(page, site, sid)
    final = session(site, sid); ref = final['output_asset_ref']
    assert len(calls) == 2 and RunStore(site.root, read_only=True).get_run(final['run_id'])['operation'] == 'extract_abox'
    assert DesignService(site.root, read_only=True).export_revision(sid, schema['revision'], format='json') == old_json
    page.get_by_role('link', name='查看本体候选', exact=True).click()
    version = page.get_by_test_id('robotics-version')
    expect(version).to_have_attribute('data-version-id', ref['version_id'])
    expect(version).to_contain_text('未验证')
    for label, expected in [('下载完整 JSON', DesignAssets(site.root).export_json(**ref)),
                            ('下载 OWL', DesignAssets(site.root).payload(**ref))]:
        with page.expect_download() as pending:
            page.get_by_role('link', name=label, exact=True).click()
        target = site.output / pending.value.suggested_filename; pending.value.save_as(target)
        assert target.read_bytes() == expected
        site.evidence['downloads'].append({'name': target.name, 'bytes': len(expected)})
    page.goto(site.url + '/#build?recipe=robotics&session=' + sid)
    wait_design(page, sid, 'abox', 'completed')
    # DR-161: graph history is explicitly read-only; historical bytes remain unchanged.
    expect(page.get_by_label('设计修订', exact=True)).to_be_visible()
    assert DesignService(site.root, read_only=True).export_revision(sid, schema['revision'], format='json') == old_json
    assert session(site, sid)['revision'] == final['revision'] and len(calls) == 2
    click_operation(page, '返回类别修改', '/edit-classes')
    changed = session(site, sid)
    assert changed['stage'] == 'classes' and changed['output_asset_ref'] is None
    assert changed['class_confirmation'] is None and changed['schema_confirmation'] is None
    assert DesignAssets(site.root).export_json(**ref) and len(calls) == 2
    done(site)


def test_empty_description_waits_without_model_until_explicit_message(live):
    site, page, calls = live
    admitted = ui_admit(page, site, ''); sid = admitted['session_id']
    wait_design(page, sid, 'classes', 'needs_description')
    assert admitted['run_id'] is None and RunStore(site.root, read_only=True).list_runs() == [] and calls == []
    page.reload(); wait_design(page, sid, 'classes', 'needs_description'); assert calls == []
    page.get_by_label('建模要求', exact=True).fill('设计技能层次')
    click_operation(page, '发送建模要求', '/messages')
    wait_design(page, sid, 'classes', 'awaiting_confirmation'); assert len(calls) == 1
    done(site)


def test_real_host_executes_admitted_round_without_manual_claim(live):
    site, page, calls = live
    state = site.admit()
    sid = state['session_id']
    def finished():
        row = DesignService(site.root, read_only=True).get(sid)
        return row if row['status'] == 'awaiting_confirmation' else None
    current = wait_for(finished, seconds=15)
    assert current['revision'] == 1 and len(calls) == 1
    run = RunStore(site.root, read_only=True).get_run(state['run_id'])
    assert run['status'] == 'completed' and run['operation'] == 'design_tbox'
    assert session(site, sid)['snapshot_sha256'] == current['snapshot_sha256']
    done(site)


@pytest.mark.parametrize('live', [{'responses': [{'status': 401, 'body': b'local-synthetic-auth-failure'},
    model_reply(proposals()[0])]}], indirect=True)
def test_host_model_failure_is_visible_and_only_explicit_retry_invokes_again(live):
    site, page, calls = live
    admitted = ui_admit(page, site); sid = admitted['session_id']
    panel = wait_design(page, sid, 'classes', 'failed')
    expect(panel).to_contain_text(re.compile('失败|错误'))
    failed = session(site, sid); assert failed['revision'] == 0 and failed['output_asset_ref'] is None
    assert len(calls) == 1
    before = db_rows(site.root); page.reload(); wait_design(page, sid, 'classes', 'failed')
    assert db_rows(site.root) == before and len(calls) == 1
    click_operation(page, '重试设计', '/retry')
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    current = session(site, sid)
    assert RunStore(site.root, read_only=True).get_run(current['run_id'])['retry_of'] == failed['run_id']
    assert len(calls) == 2
    assert all(r['operation'] == 'design_tbox' for r in RunStore(site.root, read_only=True).list_runs())
    done(site)


def prepare_waiting(root):
    return Harness(root).classes()


@pytest.mark.parametrize('live', [{'prepare': prepare_waiting}], indirect=True)
def test_waiting_session_reload_and_owned_server_restart_are_readonly(live):
    site, page, calls = live; state = site.prepared; sid = state['session_id']
    before, files = db_rows(site.root), blob_bytes(site.root)
    page.goto(site.url + '/#build?recipe=robotics&session=' + sid)
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    site.restart(); page.reload(); wait_design(page, sid, 'classes', 'awaiting_confirmation')
    assert db_rows(site.root) == before and blob_bytes(site.root) == files
    assert calls == [] and site.design_posts() == []
    done(site)


def prepare_interrupted(root):
    h = Harness(root)
    state = h.submit(description='启动前失联的设计请求')
    assert h.service.claim_next()['run_id'] == state['run_id']
    return state


@pytest.mark.parametrize('live', [{'prepare': prepare_interrupted}], indirect=True)
def test_real_host_recovers_running_design_without_automatic_model_replay(live):
    site, page, calls = live; state = site.prepared; sid = state['session_id']
    def recovered():
        row = DesignService(site.root, read_only=True).get(sid)
        return row if row['status'] == 'interrupted' else None
    saved = wait_for(recovered, seconds=4)
    assert saved['revision'] == 0 and calls == []
    # Seeded identity uses model.invalid, so a local endpoint must be rejected, never silently swapped.
    with pytest.raises(HTTPError) as caught:
        site.post_json('/api/robotics/design-sessions/' + sid + '/retry',
            {'request_id': 'new-endpoint', 'expected_revision': 0, 'failed_run_id': state['run_id']})
    assert caught.value.code == 409
    assert json.loads(caught.value.read())['error']['code'] == 'configuration_changed' and calls == []
    assert len(RunStore(site.root, read_only=True).list_runs()) == 1
    done(site)


def prepare_candidate(root):
    return Harness(root).candidate()


def prepare_incomplete_tbox(root):
    from tests.robotics_application_integration_support import candidate
    return candidate(root, missing_role='ParameterBinding')[1]


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_design_candidate_library_graph_and_application_filter(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    before = db_rows(site.root)
    page.goto(site.url + '/#library')
    card = page.locator('[data-ontology-id="' + ref['ontology_id'] + '"]')
    expect(card).to_contain_text('设计测试')
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=relations')
    expect(page.locator('#rv_canvas canvas, #rv_canvas svg')).to_be_visible()
    # DR-153: unassessed/incomplete candidates retain the application diagnosis entry.
    expect(page.get_by_role('link', name='基于此本体生成行为树', exact=True)).to_be_visible()
    expect(page.locator('#rv_host')).to_contain_text('尚未检查')
    page.goto(site.url + '/#robotics-task')
    expect(page.get_by_label('选择机器人本体', exact=True)).to_be_visible()
    assert page.locator('#rta_asset option[value="' + ref['ontology_id'] + '"]').count() == 1
    assert not [r for r in site.evidence['requests'] if '/api/application-checks' in r['url'] and r['method'] == 'POST']
    assert db_rows(site.root) == before and calls == []
    done(site)


def application_posts(site):
    return [r for r in site.evidence['requests'] if r['method'] == 'POST'
            and '/api/application-checks' in r['url']]


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
@pytest.mark.parametrize('entry', ['detail', 'graph'])
def test_application_click_checks_once_and_reload_only_reads_history(live, entry):
    """DR-153: real HTTP/SQLite/browser; no route fulfillment or synthetic application response."""
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    suffix = '?asset=' + ref['ontology_id'] + '&version=' + ref['version_id']
    page.goto(site.url + ('/#library' if entry == 'detail' else '/#robotics-view') + suffix)
    name = re.compile('^(基于此本体)?生成行为树')
    action = page.get_by_role('link', name=name)
    expect(action).to_be_visible()
    assert application_posts(site) == []
    before, files = db_rows(site.root), blob_bytes(site.root)
    with page.expect_response(lambda r: r.request.method == 'POST' and
                             r.url == site.url + '/api/application-checks', timeout=30000) as pending:
        action.click()
    assert pending.value.status == 201, pending.value.text()
    sent = pending.value.request.post_data_json
    assert sent['entrypoint'] == 'robotics.generate_behavior_tree' and sent['asset_refs'] == [ref]
    assert 'application_id' not in sent and 'generation_policy' not in sent['context']
    report = pending.value.json()['report']; cid = report['check_id']
    assert report['preparation_status'] == 'blocked' and report['execution_status'] == 'not_evaluated'
    panel = page.get_by_test_id('application-check')
    expect(panel).to_have_attribute('data-check-id', cid)
    expect(panel).to_contain_text('还需补齐')
    assert page.get_by_label('选择应用', exact=True).count() == 0
    assert panel.locator('[data-repair-availability="unavailable"] a[href]').count() == 0
    assert not [r for r in site.evidence['requests'] if r['method'] == 'POST'
                and ('/api/robotics/tasks' in r['url'] or '/repairs' in r['url'])]
    after = db_rows(site.root)
    assert all(after[t] == rows for t, rows in before.items() if not t.startswith('application_'))
    assert blob_bytes(site.root) == files and len(application_posts(site)) == 1 and calls == []
    # Refresh and a real owned-server restart must retain the exact report without POST/check/model replay.
    page.reload(); expect(page.get_by_test_id('application-check')).to_have_attribute('data-check-id', cid)
    site.restart(); page.reload()
    expect(page.get_by_test_id('application-check')).to_have_attribute('data-check-id', cid)
    assert db_rows(site.root) == after and len(application_posts(site)) == 1 and calls == []
    page.goto(site.url + '/#library' + suffix)
    expect(page.get_by_role('link', name=name)).to_be_visible()
    assert len(application_posts(site)) == 1
    done(site)


def prepare_offline_candidate(root):
    from tests.robotics_governance_support import actual_build
    return actual_build(root)[1]


@pytest.mark.parametrize('live', [{'prepare': prepare_offline_candidate, 'offline': True}], indirect=True)
def test_application_without_task_never_claims_execution_ready(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    page.goto(site.url + '/#library?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    with page.expect_response(lambda r: r.request.method == 'POST' and
                             r.url == site.url + '/api/application-checks', timeout=30000) as pending:
        page.get_by_role('link', name=re.compile('^(基于此本体)?生成行为树')).click()
    assert pending.value.status == 201
    report = pending.value.json()['report']
    assert report['preparation_status'] == 'available' and report['execution_status'] == 'not_evaluated'
    assert report['task_ref'] is None and report['execution_readiness'] == 'not_verified'
    panel = page.get_by_test_id('application-check')
    expect(panel).to_contain_text('可准备任务')
    expect(panel).not_to_contain_text('已可生成')
    assert len(application_posts(site)) == 1 and calls == []
    assert not [r for r in RunStore(site.root, read_only=True).list_runs() if r['operation'] == 'generate_behavior_tree']
    done(site)


def prepare_revisions(root):
    from tests.robotics_design_support import reply
    h = Harness(root); state = h.classes()
    proposal = proposals()[0]
    proposal['classes'][0]['definition'] = '测试修订：补充技能能力说明。'
    h.runtime.responses.append(reply(proposal)); h.message(state); h.run()
    return h.get(state)


@pytest.mark.parametrize('live', [{'prepare': prepare_revisions}], indirect=True)
def test_current_design_has_no_revision_selector_and_preserves_history(live):
    site, page, calls = live; state = site.prepared; sid = state['session_id']
    before = db_rows(site.root)
    old = DesignService(site.root, read_only=True).get_revision(sid, 1)['state']['result']['classes'][0]['definition']
    page.goto(site.url + '/#build?recipe=robotics&session=' + sid)
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    expect(page.get_by_label('设计修订', exact=True)).to_be_visible()
    expect(page.get_by_test_id('robotics-design-diff')).to_have_count(0)
    expect(page.get_by_test_id('robotics-design')).to_contain_text('测试修订：补充技能能力说明。')
    expect(page.get_by_role('button', name='确认类别', exact=True)).to_be_enabled()
    assert DesignService(site.root, read_only=True).get_revision(sid, 1)['state']['result']['classes'][0]['definition'] == old
    assert db_rows(site.root) == before and session(site, sid)['revision'] == 2 and calls == []
    page.get_by_label('设计修订', exact=True).select_option('1')
    expect(page.get_by_test_id('robotics-design-diff')).to_be_visible()
    expect(page.get_by_role('button', name='确认类别', exact=True)).to_have_count(0)
    assert db_rows(site.root) == before and site.design_posts() == []
    page.get_by_label('设计修订', exact=True).select_option('current')
    expect(page.get_by_role('button', name='确认类别', exact=True)).to_be_enabled()
    assert site.design_posts() == []
    done(site)


def malicious_reply():
    proposal = proposals()[0]
    proposal['reply'] = '<img src=x onerror="window.__designInjected=1">'
    return model_reply(proposal)


@pytest.mark.parametrize('live', [{'responses': [malicious_reply()]}], indirect=True)
def test_model_text_is_displayed_without_executing_html(live):
    site, page, calls = live
    admitted = ui_admit(page, site)
    panel = wait_design(page, admitted['session_id'], 'classes', 'awaiting_confirmation')
    expect(panel).to_contain_text('<img src=x')
    assert page.evaluate('window.__designInjected || null') is None
    assert panel.locator('img[src="x"]').count() == 0 and len(calls) == 1
    done(site)


# IR-026 T04 integration RED — DR-154. Existing browser assertions above remain unchanged.
def test_application_adaptation_build_enters_library_without_application_selection(live):
    site, page, calls = live
    admitted = ui_admit(page, site); sid = admitted['session_id']
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input')
    page.get_by_label('建模要求', exact=True).fill('确认通用关系和属性')
    click_operation(page, '发送建模要求', '/messages')
    wait_design(page, sid, 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    wait_completed_design(page, site, sid)
    ref = session(site, sid)['output_asset_ref']
    page.get_by_role('link', name='查看本体候选', exact=True).click()
    expect(page.get_by_test_id('robotics-version')).to_have_attribute('data-version-id', ref['version_id'])
    expect(page.get_by_role('link', name=re.compile('^(基于此本体)?生成行为树'))).to_be_visible()
    assert page.get_by_label('选择应用', exact=True).count() == 0
    assert db_rows(site.root)['application_checks'] == [] and application_posts(site) == []
    assert not [r for r in site.evidence['requests'] if r['method'] == 'POST' and '/application-adaptations' in r['url']]
    assert len(calls) == 2
    done(site)


def application_repair(page, action_id):
    report = page.get_by_test_id('application-check')
    button = report.locator('[data-action-id="' + action_id + '"]')
    expect(button).to_be_visible()
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/repairs')) as pending:
        button.click()
    assert pending.value.status == 200, pending.value.text()
    return pending.value.json()


def assess_candidate(page, site, ref, *, diagnosis=True):
    page.goto(site.url + '/#library?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    with page.expect_response(lambda r: r.request.method == 'POST' and
                             r.url == site.url + '/api/application-checks', timeout=30000) as pending:
        page.get_by_role('link', name=re.compile('^(基于此本体)?生成行为树')).click()
    assert pending.value.status == 201, pending.value.text()
    if diagnosis:
        expect(page.get_by_test_id('application-check')).to_be_visible()
    return pending.value.json()['report']


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_application_adaptation_auto_matches_and_reload_never_resubmits(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    assess_candidate(page, site, ref, diagnosis=False)
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-status', 'idle', timeout=40000)
    rows = db_rows(site.root)['robotics_adaptations']; assert len(rows) == 1
    aid = rows[0]['adaptation_id']
    saved = site.get('/api/robotics/application-adaptations/' + aid)
    assert saved['matching']['method'] == 'automatic' and saved['role_map']['confirmed']
    expect(panel.get_by_role('button', name='确认角色对应', exact=True)).to_have_count(0)
    expect(panel.get_by_role('button', name='生成应用版本', exact=True)).to_have_count(0)
    expect(panel.get_by_role('button', name='确认并生成应用版本', exact=True)).to_have_count(0)
    before, files = db_rows(site.root), blob_bytes(site.root)
    before_posts = len([r for r in site.evidence['requests'] if r['method'] == 'POST'])
    page.reload(); expect(panel).to_have_attribute('data-status', 'idle')
    site.restart(); page.reload()
    expect(panel).to_have_attribute('data-version-id', saved['output_asset_ref']['version_id'])
    expect(panel).to_have_attribute('data-status', 'idle')
    assert db_rows(site.root) == before and blob_bytes(site.root) == files and calls == []
    assert len([r for r in site.evidence['requests'] if r['method'] == 'POST']) == before_posts
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_application_adaptation_browser_worker_downloads_and_returns_to_existing_task_ui(live):
    from tests.robotics_application_integration_support import current_bundle
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    old_json = DesignAssets(site.root).export_json(**ref)
    assess_candidate(page, site, ref, diagnosis=False)
    expect(page.get_by_test_id('robotics-task')).to_have_attribute('data-status', 'idle', timeout=40000)
    rows = db_rows(site.root)['robotics_adaptations']; assert len(rows) == 1
    saved = site.get('/api/robotics/application-adaptations/' + rows[0]['adaptation_id'])
    assert saved['run_id'] and saved['matching']['method'] == 'automatic'
    assert not any(r['method'] == 'POST' and (r['url'].endswith('/confirm-roles') or r['url'].endswith('/submit')) for r in site.evidence['requests'])
    new = saved['output_asset_ref']; raw, document = current_bundle(site.root, new)
    page.goto(site.url + '/#library?asset=' + new['ontology_id'] + '&version=' + new['version_id'])
    version = page.get_by_test_id('robotics-version')
    expect(version).to_have_attribute('data-version-id', new['version_id'])
    for label, expected in [('下载完整 JSON', json.dumps(document, ensure_ascii=False)), ('下载 OWL', raw)]:
        with page.expect_download() as pending:
            page.get_by_role('link', name=label, exact=True).click()
        target = site.output / pending.value.suggested_filename; pending.value.save_as(target)
        if label == '下载完整 JSON': assert json.loads(target.read_bytes()) == json.loads(expected)
        else: assert target.read_bytes() == expected
        site.evidence['downloads'].append({'name': target.name, 'bytes': target.stat().st_size})
    assert DesignAssets(site.root).export_json(**ref) == old_json and calls == []
    # DR-155: user-approved direct entry supersedes the old successful-check hop.
    report = assess_candidate(page, site, new, diagnosis=False)
    assert report['preparation_status'] == 'available' and report['execution_status'] == 'not_evaluated'
    task = page.get_by_test_id('robotics-task')
    expect(task).to_have_attribute('data-version-id', new['version_id'])
    expect(task).to_have_attribute('data-status', 'idle')
    expect(task.get_by_label('任务描述', exact=True)).to_be_visible()
    expect(task).to_contain_text('可准备任务')
    expect(task).to_have_attribute('data-check-id', report['check_id'])
    expect(page.get_by_role('link', name='继续描述任务', exact=True)).to_have_count(0)
    assert calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_application_adaptation_missing_tbox_opens_class_revision_without_model_call(live):
    site, page, calls = live; original = site.prepared; ref = original['output_asset_ref']
    assess_candidate(page, site, ref); before = db_rows(site.root)
    application_repair(page, 'robotics.revise_tbox')
    wait_design(page, original['session_id'], 'classes', 'awaiting_input')
    current = session(site, original['session_id'])
    assert current['revision'] == original['revision'] + 1
    assert current['class_confirmation'] is None and calls == []
    after = db_rows(site.root)
    for table in ('versions', 'sources', 'runs'):
        assert after[table] == before[table]
    assert len(after['design_revisions']) == len(before['design_revisions']) + 1
    expect(page.get_by_role('button', name='返回类别修改', exact=True)).to_be_visible()
    done(site)


def prepare_business_task(root):
    from domain_runs.robotics_task_service import TaskService
    from tests.robotics_task_support import fixture, model, start, message
    _, version, _ = fixture(root)
    client, _ = model(); tasks = TaskService(root, model_client=client, generation_policy='offline')
    state = message(tasks, start(tasks, version))
    assert state['status'] == 'needs_information' and any(g['kind'] == 'mapping' for g in state['gaps'])
    return state


@pytest.mark.parametrize('live', [{'prepare': prepare_business_task}], indirect=True)
def test_application_adaptation_complete_task_reuses_same_session_and_business_mapping_forms(live):
    from tests.robotics_application_integration_support import check_body
    site, page, calls = live; task = site.prepared
    ref = {k: task[k] for k in ('ontology_id', 'version_id')}
    status, reply = site.post_json('/api/application-checks', check_body(ref, task_ref={'session_id': task['session_id']}))
    assert status == 201
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] +
              '&check=' + reply['report']['check_id'])
    expect(page.get_by_test_id('application-check')).to_be_visible()
    before, files = db_rows(site.root), blob_bytes(site.root)
    application_repair(page, 'robotics.complete_task')
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-session-id', task['session_id'])
    expect(panel).to_have_attribute('data-version-id', task['version_id'])
    expect(panel).to_have_attribute('data-status', 'needs_information')
    mappings = [g for g in task['gaps'] if g['kind'] == 'mapping']
    for gap in mappings:
        card = panel.locator('[data-testid="task-gap"][data-gap-id="' + gap['id'] + '"]')
        expect(card.get_by_role('combobox', name='关联对象', exact=True)).to_be_visible()
        expect(card.get_by_role('button', name='新建关联对象', exact=True)).to_be_visible()
    expect(panel.get_by_role('button', name='保存并继续', exact=True)).to_be_visible()
    assert calls == []
    after = db_rows(site.root)
    for table in ('assets', 'versions', 'sources', 'runs', 'robotics_task_sessions'):
        assert after[table] == before[table]
    assert blob_bytes(site.root) == files
    done(site)


def incomplete_repair_responses():
    from copy import deepcopy
    classes, properties = deepcopy(proposals())
    classes['classes'] = [c for c in classes['classes'] if c['id'] != 'ParameterBinding']
    for kind in ('object_properties', 'data_properties'):
        properties[kind] = [p for p in properties[kind] if not any('ParameterBinding' in p.get(field, [])
            for field in ('domain_ids', 'range_ids', 'class_ids'))]
    return [model_reply(classes), model_reply(properties), model_reply(proposals()[0])]


@pytest.mark.parametrize('live', [{'responses': incomplete_repair_responses()}], indirect=True)
def test_schema_gap_completion_requires_tab_then_explicit_send_and_preserves_stage(live):
    site, page, calls = live
    initial = ui_admit(page, site)
    wait_design(page, initial['session_id'], 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, initial['session_id'], 'properties', 'awaiting_input')
    click_operation(page, '开始关系与属性设计', '/messages')
    wait_design(page, initial['session_id'], 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    wait_completed_design(page, site, initial['session_id'])
    original = session(site, initial['session_id']); ref = original['output_asset_ref']
    original_json = DesignAssets(site.root).export_json(**ref)
    initial_calls = len(calls)
    report = assess_candidate(page, site, ref)
    check = page.get_by_test_id('application-check')
    expect(check).to_contain_text('ParameterBinding')
    expect(check).to_contain_text('hasBinding')
    application_repair(page, 'robotics.revise_tbox')
    wait_design(page, original['session_id'], 'classes', 'awaiting_input')
    expect(page.get_by_test_id('schema-repair-hints')).to_have_count(0)
    assert 'check=' + report['check_id'] in page.url
    editor = page.get_by_label('建模要求', exact=True)
    expect(editor).to_have_attribute('placeholder', re.compile('ParameterBinding.*Tab'))
    expect(editor).to_have_value('')
    before = db_rows(site.root)
    posts = len(site.design_posts())
    editor.focus(); editor.press('Tab')
    completion = editor.input_value()
    assert 'ParameterBinding' in completion and '类别' in completion
    assert len(site.design_posts()) == posts and len(calls) == initial_calls
    assert db_rows(site.root) == before
    send = page.get_by_role('button', name='确认并发送建模要求', exact=True)
    expect(send).to_be_enabled()
    expect(editor).to_have_value(completion)
    edited = completion + '\n请保留其它已有类别。'
    editor.fill(edited)
    click_operation(page, '确认并发送建模要求', '/messages')
    wait_design(page, original['session_id'], 'classes', 'awaiting_confirmation')
    assert len(calls) == initial_calls + 1
    assert json.dumps(edited, ensure_ascii=False) in calls[-1]['payload']['messages'][0]['content']
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, original['session_id'], 'properties', 'awaiting_input')
    expect(editor).to_have_attribute('placeholder', re.compile('hasBinding.*Tab'))
    expect(editor).to_have_value('')
    editor.focus(); editor.press('Tab')
    assert 'hasBinding' in editor.input_value() and '关系' in editor.input_value()
    assert len(calls) == initial_calls + 1
    assert session(site, original['session_id'])['stage'] == 'properties'
    assert site.get('/api/ontology-assets/' + ref['ontology_id'] + '/versions/' + ref['version_id'])
    # The application snapshot is still the original version until a new schema is confirmed.
    assert session(site, original['session_id'])['output_asset_ref'] is None
    assert DesignAssets(site.root).export_json(**ref) == original_json
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_repair_hint_tab_keeps_user_text_and_shift_tab_and_restores_after_reload(live):
    site, page, calls = live; original = site.prepared
    assess_candidate(page, site, original['output_asset_ref'])
    application_repair(page, 'robotics.revise_tbox')
    wait_design(page, original['session_id'], 'classes', 'awaiting_input')
    editor = page.get_by_label('建模要求', exact=True)
    expect(editor).to_have_attribute('placeholder', re.compile('ParameterBinding'))
    expect(page.get_by_role('button', name='填入修订建议', exact=True)).to_have_count(0)
    editor.fill('我自己的建模要求'); editor.press('Tab')
    expect(editor).to_have_value('我自己的建模要求')
    editor.fill(''); editor.press('Shift+Tab')
    expect(editor).to_have_value('')
    assert calls == []
    before, files = db_rows(site.root), blob_bytes(site.root)
    posts = len(site.design_posts())
    page.reload(); wait_design(page, original['session_id'], 'classes', 'awaiting_input')
    expect(editor).to_have_attribute('placeholder', re.compile('ParameterBinding'))
    expect(editor).to_have_value('')
    assert len(site.design_posts()) == posts and calls == []
    assert db_rows(site.root) == before and blob_bytes(site.root) == files
    done(site)


def prepare_two_repair_designs(root):
    from tests.robotics_application_integration_support import candidate
    first = candidate(root, missing_role='ParameterBinding')[1]
    second = Harness(root).submit(request_id='second-design')
    return {'first': first, 'second': second}


@pytest.mark.parametrize('live', [{'prepare': prepare_two_repair_designs}], indirect=True)
def test_repair_context_cannot_show_another_designs_gaps(live):
    site, page, calls = live
    first, second = site.prepared['first'], site.prepared['second']
    report = assess_candidate(page, site, first['output_asset_ref'])
    before, files = db_rows(site.root), blob_bytes(site.root)
    page.goto(site.url + '/#build?recipe=robotics&session=' + second['session_id'] + '&check=' + report['check_id'])
    wait_design(page, second['session_id'], 'classes', 'needs_description')
    expect(page.get_by_test_id('schema-repair-context-error')).to_contain_text('不属于当前设计')
    expect(page.get_by_test_id('schema-repair-hints')).to_have_count(0)
    assert calls == [] and db_rows(site.root) == before and blob_bytes(site.root) == files
    done(site)


def property_only_repair_responses():
    from copy import deepcopy
    classes, properties = deepcopy(proposals())
    properties['data_properties'] = [p for p in properties['data_properties'] if p['id'] != 'cppType']
    return [model_reply(classes), model_reply(properties), model_reply(proposals()[1]), model_reply(proposals()[1])]


@pytest.mark.parametrize('live', [{'responses': property_only_repair_responses()}], indirect=True)
def test_cpp_type_gap_opens_properties_without_reconfirming_classes_or_calling_model(live):
    site, page, calls = live
    initial = ui_admit(page, site)
    wait_design(page, initial['session_id'], 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, initial['session_id'], 'properties', 'awaiting_input')
    click_operation(page, '开始关系与属性设计', '/messages')
    wait_design(page, initial['session_id'], 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    wait_completed_design(page, site, initial['session_id'])
    original = session(site, initial['session_id']); ref = original['output_asset_ref']
    old_json = DesignAssets(site.root).export_json(**ref)
    report = assess_candidate(page, site, ref)
    assert any(i['expected'].get('role') == 'cppType' for i in report['issues'])
    initial_calls = len(calls)
    reply = application_repair(page, 'robotics.revise_tbox')
    assert reply['design_repair']['stage'] == 'properties'
    wait_design(page, original['session_id'], 'properties', 'awaiting_input')
    current = session(site, original['session_id'])
    assert current['class_confirmation'] == original['class_confirmation']
    assert current['result'] == original['result'] and len(calls) == initial_calls
    before, files = db_rows(site.root), blob_bytes(site.root)
    page.reload(); wait_design(page, original['session_id'], 'properties', 'awaiting_input')
    assert db_rows(site.root) == before and blob_bytes(site.root) == files and len(calls) == initial_calls
    editor = page.get_by_label('建模要求', exact=True)
    expect(editor).to_have_attribute('placeholder', re.compile('cppType.*Tab'))
    expect(page.get_by_test_id('schema-repair-hints')).to_have_count(0)
    editor.focus(); editor.press('Tab')
    assert 'cppType' in editor.input_value() and '保持已确认类别不变' in editor.input_value()
    assert len(calls) == initial_calls
    click_operation(page, '确认并发送建模要求', '/messages')
    wait_design(page, original['session_id'], 'properties', 'awaiting_confirmation')
    assert len(calls) == initial_calls + 1
    assert session(site, original['session_id'])['class_confirmation'] == original['class_confirmation']
    assert any(p['id'] == 'cppType' for p in session(site, original['session_id'])['result']['data_properties'])
    confirmation = page.get_by_test_id('design-confirmation')
    expect(confirmation.get_by_role('button', name='确认骨架并构建实例', exact=True)).to_be_visible()
    assert confirmation.bounding_box()['y'] < 450
    expect(editor).not_to_be_visible()
    expect(editor).to_have_attribute('placeholder', '可继续输入修改意见')
    page.get_by_text('继续提修改意见', exact=True).click()
    expect(editor).to_be_visible()
    editor.focus(); editor.press('Tab')
    expect(editor).to_have_value('')
    assert len(calls) == initial_calls + 1
    editor.fill('请保留 cppType，并复核其余数据属性。')
    click_operation(page, '确认并发送建模要求', '/messages')
    wait_design(page, original['session_id'], 'properties', 'awaiting_confirmation')
    assert len(calls) == initial_calls + 2
    expect(editor).not_to_be_visible()
    expect(confirmation).to_be_visible()
    page.reload(); wait_design(page, original['session_id'], 'properties', 'awaiting_confirmation')
    expect(editor).to_have_attribute('placeholder', '可继续输入修改意见')
    expect(editor).not_to_be_visible()
    assert DesignAssets(site.root).export_json(**ref) == old_json
    done(site)


def prepare_property_only_candidate(root):
    h = Harness(root); state = h.classes()
    state = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    properties = proposals()[1]
    properties['data_properties'] = [p for p in properties['data_properties'] if p['id'] != 'cppType']
    h.runtime.responses.append((True, json.dumps(properties, ensure_ascii=False)))
    h.message(state); h.run(); state = h.get(state)
    h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    h.run()
    return h.get(state)


@pytest.mark.parametrize('live', [{'prepare': prepare_property_only_candidate}], indirect=True)
def test_adaptation_property_repair_link_also_opens_properties_and_reload_does_not_edit(live):
    site, page, calls = live; original = site.prepared; ref = original['output_asset_ref']
    status, adaptation = site.post_json('/api/robotics/application-adaptations',
        {**ref, 'mode': 'real', 'task_ref': None, 'request_id': 'property-repair'})
    assert status == 201 and adaptation['status'] == 'needs_tbox_revision'
    page.goto(site.url + '/#robotics-task?adaptation=' + adaptation['adaptation_id'])
    expect(page.get_by_test_id('robotics-application-adaptation')).to_be_visible()
    page.get_by_role('link', name='补充缺失定义', exact=True).click()
    wait_design(page, original['session_id'], 'properties', 'awaiting_input')
    assert session(site, original['session_id'])['class_confirmation'] == original['class_confirmation']
    assert calls == []
    before, files = db_rows(site.root), blob_bytes(site.root)
    page.reload(); wait_design(page, original['session_id'], 'properties', 'awaiting_input')
    expect(page.get_by_label('建模要求', exact=True)).to_have_attribute('placeholder', re.compile('cppType.*Tab'))
    assert db_rows(site.root) == before and blob_bytes(site.root) == files and calls == []
    done(site)


def gated_class_response():
    return {**model_reply(proposals()[0]), 'entered': threading.Event(), 'release': threading.Event()}


@pytest.mark.parametrize('live', [{'responses': [gated_class_response()]}], indirect=True)
def test_model_running_shows_animation_and_completion_promotes_confirmation(live, request):
    site, page, calls = live
    gate = request.node.callspec.params['live']['responses'][0]
    try:
        original = ui_admit(page, site)
        assert gate['entered'].wait(timeout=3)
        panel = wait_design(page, original['session_id'], 'classes', 'running')
        progress = page.get_by_test_id('design-progress')
        expect(progress).to_be_visible()
        expect(progress).to_contain_text('模型')
        expect(progress).to_contain_text('请稍候')
        expect(panel).to_have_attribute('aria-busy', 'true')
        spinner = progress.locator('.rd-progress-spinner')
        assert spinner.evaluate('node=>getComputedStyle(node).animationName') != 'none'
        expect(page.get_by_role('button', name='确认类别', exact=True)).to_have_count(0)
        expect(page.get_by_label('建模要求', exact=True)).not_to_be_visible()
        assert len(calls) == 1
    finally:
        gate['release'].set()
    wait_design(page, original['session_id'], 'classes', 'awaiting_confirmation')
    expect(progress).not_to_be_visible()
    expect(panel).to_have_attribute('aria-busy', 'false')
    primary = page.get_by_test_id('design-confirmation')
    expect(primary.get_by_role('button', name='确认类别', exact=True)).to_be_visible()
    assert primary.bounding_box()['y'] < 450
    expect(page.get_by_label('建模要求', exact=True)).not_to_be_visible()
    page.get_by_text('继续提修改意见', exact=True).click()
    expect(page.get_by_label('建模要求', exact=True)).to_be_visible()
    assert len(calls) == 1
    done(site)


def prepare_custom_role_candidate(root):
    from tests.robotics_auto_adaptation_support import custom_candidate
    with pytest.MonkeyPatch.context() as patch:
        return custom_candidate(root, patch)[1]


@pytest.mark.parametrize('live', [{'prepare': prepare_custom_role_candidate}], indirect=True)
def test_unresolved_role_allows_manual_selection_and_one_confirm_generate(live):
    site, page, calls = live; original = site.prepared; ref = original['output_asset_ref']
    before_runs = len(RunStore(site.root, read_only=True).list_runs())
    old_json = DesignAssets(site.root).export_json(**ref)
    assess_candidate(page, site, ref)
    reply = application_repair(page, 'robotics.adapt_execution')
    panel = page.get_by_test_id('robotics-application-adaptation')
    expect(panel).to_have_attribute('data-status', 'needs_tbox_revision')
    source = DesignAssets(site.root).detail(**ref)['tbox']['namespace'] + 'CallSite'
    choose = panel.get_by_role('combobox', name='对应内容：技能的调用信息', exact=True)
    expect(choose).to_have_value('')
    submit = panel.get_by_role('button', name='确认并生成应用版本', exact=True)
    expect(submit).to_be_disabled()
    assert len(RunStore(site.root, read_only=True).list_runs()) == before_runs and calls == []
    page.reload(); expect(panel).to_have_attribute('data-status', 'needs_tbox_revision')
    assert len(RunStore(site.root, read_only=True).list_runs()) == before_runs
    choose.select_option(source)
    expect(submit).to_be_enabled()
    queued = click_operation(page, '确认并生成应用版本', '/submit')
    assert queued['matching']['method'] == 'manual' and queued['status'] == 'queued'
    task = page.get_by_test_id('robotics-task')
    expect(task).to_have_attribute('data-status', 'idle', timeout=40000)
    saved = site.get('/api/robotics/application-adaptations/' + reply['target']['resource_id'])
    assert saved['run_id'] == queued['run_id']
    expect(task).to_have_attribute('data-version-id', saved['output_asset_ref']['version_id'])
    assert len(RunStore(site.root, read_only=True).list_runs()) == before_runs + 1
    assert DesignAssets(site.root).export_json(**ref) == old_json and calls == []
    assert not any(r['method'] == 'POST' and r['url'].endswith('/confirm-roles') for r in site.evidence['requests'])
    done(site)


# DR-166: user-visible workflow continuity, using actual persisted services.
@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_application_home_checks_incompatible_candidate_and_opens_its_revision(live):
    site, page, calls = live
    original = site.prepared; ref = original['output_asset_ref']
    page.goto(site.url + '/#robotics-task')
    page.get_by_label('选择机器人本体', exact=True).select_option(ref['ontology_id'])
    page.get_by_role('button', name='开始新任务', exact=True).click()
    check = page.get_by_test_id('application-check')
    expect(check).to_contain_text('ParameterBinding')
    expect(page.get_by_role('button', name='重试准备任务', exact=True)).to_have_count(0)
    before = len(application_posts(site))
    page.reload(); expect(check).to_contain_text('ParameterBinding')
    assert len(application_posts(site)) == before and calls == []
    application_repair(page, 'robotics.revise_tbox')
    panel = wait_design(page, original['session_id'], 'classes', 'awaiting_input')
    expect(panel.get_by_label('建模要求', exact=True)).to_be_visible()
    assert session(site, original['session_id'])['output_asset_ref'] is None
    assert DesignAssets(site.root).payload(**ref)
    done(site)


def assert_compact_design_controls(page):
    panel = page.get_by_test_id('robotics-design')
    graph = panel.get_by_test_id('interactive-ontology-graph-panel')
    expect(graph.locator('svg [data-node-id]').first).to_be_visible()
    expect(graph.locator('.interactive-ontology-graph-title')).to_have_count(0)
    expect(panel.locator('.rd-stage-guide')).to_be_visible()
    expect(panel.locator('.rd-design-summary')).not_to_be_visible()
    plot = graph.locator('.interactive-ontology-graph-plot').bounding_box()
    assert panel.locator('.rd-stage-guide').bounding_box()['y'] < plot['y']
    if panel.get_by_text('继续提修改意见', exact=True).is_visible():
        assert panel.get_by_text('继续提修改意见', exact=True).bounding_box()['y'] < plot['y']
        panel.get_by_text('继续提修改意见', exact=True).click()
    editor = panel.get_by_label('建模要求', exact=True)
    expect(editor).to_be_visible()
    assert editor.bounding_box()['y'] + editor.bounding_box()['height'] <= graph.bounding_box()['y']
    expect(panel.get_by_label('设计修订', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')


@pytest.mark.parametrize('width', [1440, 390])
def test_design_steps_put_compact_controls_before_canvas_and_keep_definitions(live, width):
    site, page, calls = live
    page.set_viewport_size({'width': width, 'height': 1000})
    sid = ui_admit(page, site)['session_id']
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    assert_compact_design_controls(page)
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input')
    assert_compact_design_controls(page)
    click_operation(page, '开始关系与属性设计', '/messages')
    panel = wait_design(page, sid, 'properties', 'awaiting_confirmation')
    assert_compact_design_controls(page)
    panel.get_by_text('查看完整定义与说明', exact=True).click()
    expect(panel.get_by_text('查看关系设计详情', exact=False)).to_be_visible()
    assert len(calls) == 2
    done(site)


def test_new_build_completion_opens_exact_fixed_graph_without_application_side_effects(live):
    site, page, calls = live
    sid = ui_admit(page, site)['session_id']
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input')
    click_operation(page, '开始关系与属性设计', '/messages')
    wait_design(page, sid, 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    expect(page).to_have_url(re.compile(r'#robotics-view\?.*view=interactive-ontology-graph'), timeout=25000)
    final = session(site, sid); ref = final['output_asset_ref']
    assert ref['ontology_id'] in page.url and ref['version_id'] in page.url
    expect(page.get_by_test_id('interactive-ontology-graph-panel')).to_have_attribute('data-kind', 'version')
    assert db_rows(site.root)['application_checks'] == [] and len(calls) == 2
    before = db_rows(site.root)
    page.reload()
    expect(page.get_by_test_id('interactive-ontology-graph-panel')).to_have_attribute('data-kind', 'version')
    assert db_rows(site.root) == before and len(calls) == 2
    done(site)


@pytest.mark.parametrize('live', [{'responses': incomplete_repair_responses() + [model_reply(proposals()[1])]}], indirect=True)
@pytest.mark.parametrize('origin', ['check', 'adaptation'])
def test_schema_repair_completion_returns_to_application_flow_with_new_candidate(live, origin):
    site, page, calls = live
    sid = ui_admit(page, site)['session_id']
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input')
    click_operation(page, '开始关系与属性设计', '/messages')
    wait_design(page, sid, 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    expect(page).to_have_url(re.compile(r'#robotics-view\?'), timeout=25000)
    original = session(site, sid); old = original['output_asset_ref']
    before = DesignAssets(site.root).payload(**old)
    if origin == 'check':
        assess_candidate(page, site, old)
        application_repair(page, 'robotics.revise_tbox')
    else:
        from domain_runs.robotics_application_service import ApplicationService
        flow = ApplicationService(site.root).prepare(**old, mode='real', task_ref=None,
            request_id='repair-return', automatic=True)
        page.goto(site.url + '/#robotics-task?adaptation=' + flow['adaptation_id'])
        page.get_by_role('link', name='补充缺失定义', exact=True).click()
    wait_design(page, sid, 'classes', 'awaiting_input')
    assert_compact_design_controls(page)
    page.get_by_label('建模要求', exact=True).fill('补齐应用所需类别')
    click_operation(page, '确认并发送建模要求', '/messages')
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    click_operation(page, '确认类别', '/confirm-classes')
    wait_design(page, sid, 'properties', 'awaiting_input')
    page.get_by_label('建模要求', exact=True).fill('补齐应用所需关系与属性')
    click_operation(page, '确认并发送建模要求', '/messages')
    wait_design(page, sid, 'properties', 'awaiting_confirmation')
    click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    task = page.get_by_test_id('robotics-task')
    expect(task).to_have_attribute('data-status', 'idle', timeout=40000)
    final = session(site, sid); new = final['output_asset_ref']
    assert new != old and '#robotics-task?' in page.url and len(calls) == 4
    assert DesignAssets(site.root).payload(**old) == before
    rows = db_rows(site.root)
    assert len(rows['robotics_adaptations']) == 1
    if origin == 'adaptation':
        saved = site.get('/api/robotics/application-adaptations/' + flow['adaptation_id'])
        assert saved['asset_ref'] == new and saved['status'] == 'completed'
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_new_task_automatically_adapts_once_then_enters_checked_task_and_reload_is_readonly(live):
    site, page, calls = live; original = site.prepared['output_asset_ref']
    old = DesignAssets(site.root).payload(**original)
    page.goto(site.url + '/#robotics-task?asset=' + original['ontology_id'] + '&version=' + original['version_id'])
    task = page.get_by_test_id('robotics-task')
    expect(task).to_have_attribute('data-status', 'idle', timeout=40000)
    expect(task.get_by_label('任务描述', exact=True)).to_be_visible()
    expect(task).to_contain_text('可准备任务')
    records = db_rows(site.root)
    assert len(records['robotics_adaptations']) == 1
    flow = json.loads(records['robotics_adaptations'][0]['record'])
    assert flow['matching']['method'] == 'automatic' and flow['status'] == 'completed'
    expect(task).to_have_attribute('data-version-id', flow['output_asset_ref']['version_id'])
    assert DesignAssets(site.root).payload(**original) == old and calls == []
    posts = [r for r in site.evidence['requests'] if r['method'] == 'POST']
    assert not any(r['url'].endswith(('/submit', '/confirm-roles')) for r in posts)
    page.reload(); expect(task).to_have_attribute('data-status', 'idle')
    assert db_rows(site.root) == records
    assert len([r for r in site.evidence['requests'] if r['method'] == 'POST']) == len(posts)
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_readonly_application_check_does_not_start_automatic_adaptation(live):
    from tests.robotics_application_integration_support import check_body
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    _, response = site.post_json('/api/application-checks', check_body(ref))
    before = db_rows(site.root)
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] +
              '&check=' + response['report']['check_id'] + '&readonly=1')
    expect(page.get_by_test_id('application-check')).to_be_visible()
    page.reload(); expect(page.get_by_test_id('application-check')).to_be_visible()
    assert db_rows(site.root) == before and calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_failed_automatic_adaptation_stays_visible_and_explicit_retry_reuses_request(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    attempts = []
    def unavailable(route):
        attempts.append(route.request.post_data_json)
        route.fulfill(status=503, content_type='application/json',
            body=json.dumps({'error': {'code': 'storage_unavailable', 'message': '准备服务暂不可用'}}))
    page.route('**/api/application-checks/*/repairs', unavailable)
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    expect(page.get_by_role('alert')).to_contain_text('准备服务暂不可用')
    retry = page.get_by_role('button', name='重试检查', exact=True)
    expect(retry).to_be_visible()
    page.wait_for_timeout(500)
    assert len(attempts) == 1 and db_rows(site.root)['robotics_adaptations'] == []
    page.unroute('**/api/application-checks/*/repairs', unavailable)
    with page.expect_request('**/api/application-checks/*/repairs') as pending:
        retry.click()
    assert pending.value.post_data_json == attempts[0]
    expect(page.get_by_test_id('robotics-task')).to_have_attribute('data-status', 'idle', timeout=40000)
    assert len(db_rows(site.root)['robotics_adaptations']) == 1 and calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_stale_application_check_never_automatically_adapts_or_edits_design(live):
    from tests.robotics_application_integration_support import check_body
    site, page, calls = live; original = site.prepared; ref = original['output_asset_ref']
    _, response = site.post_json('/api/application-checks', check_body(ref))
    site.post_json('/api/robotics/design-sessions/' + original['session_id'] + '/edit-classes',
        {'expected_revision': original['revision'], 'request_id': 'other-window-edit'})
    before = db_rows(site.root)
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] +
              '&check=' + response['report']['check_id'])
    check = page.get_by_test_id('application-check')
    expect(check).to_contain_text('检查已过期')
    for button in check.locator('[data-action-id]').all():
        expect(button).to_be_disabled()
    assert db_rows(site.root) == before and calls == []
    done(site)


def prepare_completed_adaptation(root):
    from tests.robotics_application_integration_support import adapted
    return adapted(root)[2]


@pytest.mark.parametrize('live', [{'prepare': prepare_completed_adaptation}], indirect=True)
def test_completed_adaptation_history_is_readonly_until_explicit_continue_and_reuses_task(live):
    site, page, calls = live; flow = site.prepared
    before = db_rows(site.root)
    href = site.url + '/#robotics-task?adaptation=' + flow['adaptation_id']
    page.goto(href)
    panel = page.get_by_test_id('robotics-application-adaptation')
    expect(panel).to_have_attribute('data-status', 'completed')
    expect(panel.get_by_role('button', name='继续准备任务', exact=True)).to_be_visible()
    page.reload(); expect(panel).to_have_attribute('data-status', 'completed')
    assert db_rows(site.root) == before and calls == []
    panel.get_by_role('button', name='继续准备任务', exact=True).click()
    task = page.get_by_test_id('robotics-task')
    expect(task).to_have_attribute('data-status', 'idle', timeout=25000)
    sid = task.get_attribute('data-session-id')
    rows = db_rows(site.root)
    page.goto(href)
    panel.get_by_role('button', name='继续准备任务', exact=True).click()
    expect(task).to_have_attribute('data-session-id', sid)
    assert db_rows(site.root) == rows and calls == []
    done(site)


def prepare_adaptation_with_concurrently_changed_task(root):
    from domain_runs.robotics_task_service import TaskService
    from tests.robotics_application_integration_support import adapted, fill_task, service, finish
    _, original, initial = adapted(root)
    current, _ = fill_task(root, initial['output_asset_ref'])
    flow = service(root).prepare(**original['output_asset_ref'], mode='real',
        task_ref={'session_id': current['session_id']}, request_id='continue-existing-task', automatic=True)
    completed = finish(root, flow)
    newer = TaskService(root, generation_policy='offline').generate(current['session_id'],
        expected_revision=current['revision'], request_id='other-window-generation')
    return {'flow': completed, 'task': newer}


@pytest.mark.parametrize('live', [{'prepare': prepare_adaptation_with_concurrently_changed_task}], indirect=True)
def test_automatic_return_never_overwrites_a_concurrently_changed_task_and_offers_its_current_page(live):
    site, page, calls = live; flow, task = site.prepared['flow'], site.prepared['task']
    before = db_rows(site.root)
    page.goto(site.url + '/#robotics-task?adaptation=' + flow['adaptation_id'] + '&continue=1')
    panel = page.get_by_test_id('robotics-application-adaptation')
    expect(panel.get_by_role('alert')).not_to_be_empty()
    current = panel.get_by_role('link', name='查看当前任务', exact=True)
    expect(current).to_be_visible()
    page.wait_for_timeout(500)
    requests = [r for r in site.evidence['requests'] if r['method'] == 'POST' and r['url'].endswith('/resume-task')]
    assert len(requests) == 1 and db_rows(site.root) == before and calls == []
    current.click()
    restored = page.get_by_test_id('robotics-task')
    expect(restored).to_have_attribute('data-session-id', task['session_id'])
    expect(restored).to_have_attribute('data-version-id', task['version_id'])
    assert db_rows(site.root) == before and calls == []
    done(site)


def prepare_cpp_type_gap(root):
    from copy import deepcopy
    from tests.robotics_design_support import reply
    h = Harness(root); state = h.classes()
    state = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    properties = deepcopy(proposals()[1])
    properties['data_properties'] = [p for p in properties['data_properties'] if p['id'] != 'cppType']
    h.runtime.responses.append(reply(properties)); h.message(state); h.run(); state = h.get(state)
    h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    h.run(); return h.get(state)


@pytest.mark.parametrize('live', [{'prepare': prepare_cpp_type_gap}], indirect=True)
@pytest.mark.parametrize('width', [1440, 390])
def test_application_guidance_explains_business_gap_and_has_one_primary_action(live, width):
    site, page, calls = live; original = site.prepared
    page.set_viewport_size({'width': width, 'height': 1000})
    report = assess_candidate(page, site, original['output_asset_ref'])
    assert any(i['expected'].get('role') == 'cppType' for i in report['issues'])
    panel = page.get_by_test_id('application-check')
    visible = panel.inner_text()
    assert '参数' in visible and '数据类型' in visible and '完善本体' in visible
    assert not re.search(r'cppType|C\+\+|xsd:|PortContract', visible)
    primary = panel.get_by_role('button', name='完善本体', exact=True)
    expect(primary).to_have_count(1); expect(primary).to_be_enabled()
    expect(panel.get_by_role('button', name='修订本体骨架', exact=True)).to_have_count(0)
    expect(panel.get_by_role('button', name='核对角色对应', exact=True)).to_have_count(0)
    primary_box = primary.bounding_box()
    recheck_box = panel.get_by_role('button', name='重新检查', exact=True).bounding_box()
    assert primary_box['x'] + primary_box['width'] + 8 <= recheck_box['x'] or primary_box['y'] + primary_box['height'] + 8 <= recheck_box['y']
    expect(panel.locator('.rc-options')).to_have_count(0)
    expect(panel.get_by_role('button', name='使用已有定义', exact=True)).to_have_count(0)
    expect(panel.locator('[data-action-id]')).to_have_count(1)
    assert '执行未验证' not in panel.inner_text()
    panel.get_by_text('查看技术详情', exact=True).first.click()
    expect(panel).to_contain_text('cppType')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2') and calls == []
    done(site)


@pytest.mark.parametrize('live,action', [
    ({'prepare': prepare_incomplete_tbox}, 'robotics.revise_tbox'),
    ({'prepare': prepare_custom_role_candidate}, 'robotics.adapt_execution'),
], indirect=['live'])
def test_back_from_each_repair_refreshes_check_without_repair_loop_and_allows_reentry(live, action):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    old = assess_candidate(page, site, ref)
    application_repair(page, action)
    destination = page.get_by_test_id('robotics-design' if action.endswith('revise_tbox') else 'robotics-application-adaptation')
    expect(destination).to_be_visible()
    before = db_rows(site.root)
    assert site.get('/api/application-checks/' + old['check_id'])['freshness'] == 'stale'
    repairs = len([r for r in application_posts(site) if r['url'].endswith('/repairs')])
    page.go_back()
    panel = page.get_by_test_id('application-check'); expect(panel).to_be_visible()
    expect(panel.locator('[data-action-id="' + action + '"]').first).to_be_enabled()
    new_id = panel.get_attribute('data-check-id')
    assert new_id != old['check_id']
    assert site.get('/api/application-checks/' + new_id)['freshness'] == 'current'
    after = db_rows(site.root)
    for table in ('versions', 'runs', 'design_sessions', 'design_revisions', 'robotics_adaptations', 'robotics_task_sessions'):
        assert after[table] == before[table]
    assert len([r for r in application_posts(site) if r['url'].endswith('/repairs')]) == repairs and calls == []
    page.go_forward(); expect(destination).to_be_visible()
    page.go_back(); expect(panel).to_be_visible()
    expect(panel.locator('[data-action-id="' + action + '"]').first).to_be_enabled()
    application_repair(page, action)
    expect(destination).to_be_visible()
    assert calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
@pytest.mark.parametrize('malformed', [False, True])
def test_repair_controls_unlock_after_failure_and_reject_duplicate_inflight_actions(live, malformed):
    site, page, calls = live
    assess_candidate(page, site, site.prepared['output_asset_ref'])
    pending = []
    page.route('**/api/application-checks/*/repairs', lambda route: pending.append(route))
    panel = page.get_by_test_id('application-check')
    button = panel.get_by_role('button', name='完善本体', exact=True)
    button.click(); expect(button).to_be_disabled()
    expect(panel.get_by_role('button', name='重新检查', exact=True)).to_be_disabled()
    button.evaluate('b=>{b.click();b.click();}')
    assert len(pending) == 1
    pending[0].fulfill(status=200 if malformed else 503, content_type='application/json',
        body=json.dumps({'kind': 'unexpected'} if malformed else {'error': {'message': '服务暂不可用'}}))
    expect(page.locator('#rt_task').get_by_role('alert')).not_to_be_empty()
    expect(button).to_be_enabled()
    expect(panel.get_by_role('button', name='重新检查', exact=True)).to_be_enabled()
    assert calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_late_repair_response_cannot_navigate_away_and_back_restores_working_controls(live):
    site, page, calls = live
    assess_candidate(page, site, site.prepared['output_asset_ref'])
    pending = []
    page.route('**/api/application-checks/*/repairs', lambda route: pending.append(route))
    with page.expect_request('**/api/application-checks/*/repairs'):
        page.get_by_role('button', name='完善本体', exact=True).click()
    assert len(pending) == 1
    page.goto(site.url + '/#library')
    expect(page.get_by_test_id('ontology-catalog')).to_be_visible()
    pending[0].fulfill(response=pending[0].fetch())
    page.wait_for_timeout(300)
    assert page.url.endswith('#library') and calls == []
    page.go_back()
    expect(page.get_by_role('button', name='完善本体', exact=True)).to_be_enabled()
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_repair_revision_conflict_requires_fresh_check_instead_of_reenabling_old_action(live):
    site, page, calls = live; original = site.prepared
    report = assess_candidate(page, site, original['output_asset_ref'])
    site.post_json('/api/robotics/design-sessions/' + original['session_id'] + '/edit-classes',
        {'expected_revision': original['revision'], 'request_id': 'concurrent-browser-change'})
    panel = page.get_by_test_id('application-check')
    primary = panel.get_by_role('button', name='完善本体', exact=True)
    primary.click()
    expect(page.locator('#rt_task').get_by_role('alert')).to_contain_text('重新检查')
    expect(primary).to_be_disabled()
    expect(panel.get_by_role('button', name='重新检查', exact=True)).to_be_enabled()
    panel.get_by_role('button', name='重新检查', exact=True).click()
    expect(primary).to_be_enabled()
    assert panel.get_attribute('data-check-id') != report['check_id'] and calls == []
    done(site)


@pytest.mark.parametrize('live', [{'prepare': prepare_incomplete_tbox}], indirect=True)
def test_repair_request_timeout_unlocks_controls_without_automatic_retry(live):
    site, page, calls = live
    assess_candidate(page, site, site.prepared['output_asset_ref'])
    page.clock.install()
    pending = []
    page.route('**/api/application-checks/*/repairs', lambda route: pending.append(route))
    button = page.get_by_role('button', name='完善本体', exact=True)
    with page.expect_request('**/api/application-checks/*/repairs'):
        button.click()
    expect(button).to_be_disabled()
    page.clock.fast_forward(30001)
    expect(page.locator('#rt_task').get_by_role('alert')).to_contain_text('超时')
    expect(button).to_be_enabled()
    expect(page.get_by_role('button', name='重新检查', exact=True)).to_be_enabled()
    assert len(pending) == 1 and calls == []
    page.unroute_all(behavior='ignoreErrors')
    done(site)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path,
        default=ROOT / '.check-results/robotics-design-browser')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT'] = str(args.output.resolve())
    command = [__file__, '-q', '--tb=short', '--durations=20', '--junitxml=' + str(args.output / 'results.xml')]
    if args.workers > 1:
        command.extend(['-n', str(args.workers), '--dist', 'worksteal'])
    raise SystemExit(pytest.main(command))
