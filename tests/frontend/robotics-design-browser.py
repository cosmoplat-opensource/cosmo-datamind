"""Real Chromium + Gunicorn + persisted designs; only model HTTP scripted — DR-070 / T08 RED."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
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


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as pw:
        executable = next((p for p in (os.environ.get('DATAMIND_BROWSER_EXECUTABLE'),
            '/usr/bin/google-chrome', '/usr/bin/chromium') if p and Path(p).is_file()), None)
        instance = pw.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def live(tmp_path, request, browser):
    options = dict(getattr(request, 'param', {}))
    responses = options.pop('responses', [model_reply(p) for p in proposals()])
    offline = options.pop('offline', False)
    output = Path(os.environ.get('DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT',
        str(ROOT / '.check-results/robotics-design-browser'))) / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    with model_server(*responses) as (url, calls):
        site = DesignSite(tmp_path / 'site', output, model_url=None if offline else url, **options)
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
    wait_design(page, sid, 'abox', 'completed')
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
    page.get_by_label('设计修订', exact=True).select_option(str(schema['revision']))
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


@pytest.mark.parametrize('live', [{'prepare': prepare_candidate}], indirect=True)
def test_design_candidate_library_graph_and_application_filter(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    before = db_rows(site.root)
    page.goto(site.url + '/#library')
    card = page.locator('[data-ontology-id="' + ref['ontology_id'] + '"]')
    expect(card).to_contain_text('设计测试')
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=relations')
    expect(page.locator('#rv_canvas canvas')).to_be_visible()
    expect(page.get_by_role('link', name='基于此本体生成行为树', exact=True)).to_have_count(0)
    expect(page.locator('#rv_host')).to_contain_text(re.compile('不支持|未满足'))
    page.goto(site.url + '/#robotics-task')
    expect(page.get_by_label('选择机器人本体', exact=True)).to_be_visible()
    assert page.locator('#rta_asset option[value="' + ref['ontology_id'] + '"]').count() == 0
    assert db_rows(site.root) == before and calls == []
    done(site)


def prepare_revisions(root):
    from tests.robotics_design_support import reply
    h = Harness(root); state = h.classes()
    proposal = proposals()[0]
    proposal['classes'][0]['definition'] = '测试修订：补充技能能力说明。'
    h.runtime.responses.append(reply(proposal)); h.message(state); h.run()
    return h.get(state)


@pytest.mark.parametrize('live', [{'prepare': prepare_revisions}], indirect=True)
def test_history_diff_shows_changed_definition_without_changing_current_revision(live):
    site, page, calls = live; state = site.prepared; sid = state['session_id']
    before = db_rows(site.root)
    old = DesignService(site.root, read_only=True).get_revision(sid, 1)['state']['result']['classes'][0]['definition']
    page.goto(site.url + '/#build?recipe=robotics&session=' + sid)
    wait_design(page, sid, 'classes', 'awaiting_confirmation')
    page.get_by_label('设计修订', exact=True).select_option('1')
    diff = page.get_by_test_id('robotics-design-diff')
    expect(diff).to_contain_text(old)
    expect(diff).to_contain_text('测试修订：补充技能能力说明。')
    assert db_rows(site.root) == before and session(site, sid)['revision'] == 2 and calls == []
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path,
        default=ROOT / '.check-results/robotics-design-browser')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT'] = str(args.output.resolve())
    raise SystemExit(pytest.main([__file__, '-q', '--tb=short', '--junitxml=' + str(args.output / 'results.xml')]))
