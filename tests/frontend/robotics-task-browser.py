"""T04 RED: real browser/build/OWL/completion/compiler/download; only model HTTP is scripted — DR-063 / DR-066."""
import argparse
import os
from pathlib import Path
import re
import sys
import threading
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from playwright.sync_api import expect, sync_playwright
from rdflib import Graph, RDF

from ontology_assets import AssetStore
from tests.robotics_browser_support import LiveSite, choose_robotics, fill_robotics, submit_from_ui, wait_run_ui
from tests.robotics_support import S, config, trees
from tests.robotics_task_support import BASIS, TEXT, assert_generated, expected_xml, model_server, tree_json
from tests.storage_pipeline_support import fresh_payload


class TaskSite(LiveSite):
    def __init__(self, directory, output, *, model_url):
        self.model_url = model_url
        self.model_enabled = False
        super().__init__(directory, output)

    def start(self):
        if self.model_enabled:
            self.env.update(DATAMIND_LLM_BASE=self.model_url, DATAMIND_LLM_MODEL='scripted-test-model',
                            DATAMIND_LLM_KEY='local-model-test-key', DATAMIND_LLM_TIMEOUT='5')
        super().start()

    def enable_task_model(self):
        # DR-070: create the legacy asset offline, then configure task extraction.
        # A configured build now belongs to the new TBox/ABox flow.
        if not self.model_enabled:
            self.model_enabled = True
            self.restart()


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
    replies = getattr(request, 'param', [{} for _ in range(8)])
    output = Path(os.environ.get('DATAMIND_ROBOTICS_TASK_TEST_OUTPUT', str(ROOT / '.check-results/robotics-task-browser')))
    output = output / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    with model_server(*replies) as (url, calls):
        site = TaskSite(tmp_path / 'site', output, model_url=url)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(3500)
        page.set_default_navigation_timeout(15000)
        site.observe(page)
        try:
            yield site, page, calls
        finally:
            try:
                page.screenshot(path=str(output / 'final.png'), full_page=True)
            except Exception as exc:
                site.evidence['screenshot_error'] = str(exc)
            site.evidence['model_requests'] = [{'path': c['path'], 'payload': c['payload']} for c in calls]
            context.close()
            site.close()


def done(site):
    assert not site.evidence['page_errors']
    site.evidence['assertions_completed'] = True


def open_task(page, site, ref, *, enable_model=True):
    if enable_model:
        site.enable_task_model()
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_be_visible()
    expect(panel).to_have_attribute('data-version-id', ref['version_id'])
    return panel


def submit_task(page, panel, text=TEXT):
    panel.get_by_label('任务描述', exact=True).fill(text)
    with page.expect_response(lambda r: r.request.method == 'POST' and '/api/robotics/tasks/' in r.url
                              and r.url.endswith('/messages'), timeout=15000) as pending:
        panel.get_by_role('button', name='生成行为树', exact=True).click()
    assert pending.value.status == 200, pending.value.text()
    return pending.value.json()


def fill_gaps(panel, state):
    mappings = {'hasPerceptionProfile': str(S.Perception_Current), 'hasPlacementProfile': str(S.Placement_Current)}
    for item in state['gaps']:
        if item['kind'] == 'confirmation':
            # One explicit confirmation of the shown scope, not seven repeated forms.
            panel.get_by_role('checkbox', name='我已核对当前任务知识', exact=True).check()
            continue
        # data-gap-id is the stable binding; no IRI/hash is a user-entered field.
        card = panel.locator('[data-testid="task-gap"][data-gap-id="' + item['id'] + '"]')
        expect(card).to_be_visible()
        if item['kind'] == 'mapping':
            card.get_by_role('combobox', name='关联对象', exact=True).select_option(mappings[item['field']])
        elif item['kind'] == 'parameter':
            values = {'exposureTime': '155', 'rawValue': '[200,-900,500,1,0,0,0]'}
            assert item['field'] in values
            card.get_by_label(item['label'], exact=True).fill(values[item['field']])
        elif item['kind'] == 'applicability':
            expect(card.get_by_label('适配依据', exact=True)).to_have_value('')
            card.get_by_label('适配依据', exact=True).fill(BASIS)
            card.get_by_label('有效期', exact=True).fill('2099-01-01')
            card.get_by_role('checkbox', name='我确认以上依据适用于当前范围', exact=True).check()


def save_gaps(page, panel):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/completion')) as pending:
        panel.get_by_role('button', name='保存并继续', exact=True).click()
    assert pending.value.status == 200, pending.value.text()
    return pending.value.json()


def finish(page, panel, current):
    for _ in range(8):
        if current['status'] == 'generated': return current
        assert current['status'] == 'needs_information' and current['gaps']
        fill_gaps(panel, current)
        current = save_gaps(page, panel)
        expect(panel).to_have_attribute('data-version-id', current['version_id'])
    raise AssertionError('UI completion stages did not converge')


def download(page, site, panel, current, *, values=None):
    expect(panel.get_by_test_id('task-tree')).to_have_attribute('data-run-id', current['run_id'])
    # Each real XML node is represented in the read-only hierarchy, not an unrelated previous preview.
    expected = expected_xml(values)
    expect(panel.get_by_test_id('task-tree').locator('[data-node-tag]')).to_have_count(1 + sum(len(list(tree.iter())) for tree in expected.findall('BehaviorTree')))
    with page.expect_download() as pending:
        panel.get_by_role('link', name='下载行为树 XML', exact=True).click()
    path = site.output / pending.value.suggested_filename
    pending.value.save_as(path)
    raw = path.read_bytes()
    assert trees(ET.fromstring(raw)) == trees(expected)
    assert current['tree'] == tree_json(ET.fromstring(raw))
    assert raw == assert_generated(site.root, current, values=values)
    site.evidence['downloads'].append({'run_id': current['run_id'], 'bytes': len(raw)})
    return raw


def test_real_build_to_inline_completion_generation_download_and_restart(live):
    site, page, calls = live
    choose_robotics(page, site)
    fill_robotics(page, name='基础流程合成测试')
    rid = submit_from_ui(page, site)
    wait_run_ui(page, rid, 'completed')
    built = site.terminal(rid)
    original_ref = built['output_asset_ref']
    before = AssetStore(site.root).read_payload(**original_ref)
    site.enable_task_model()
    page.get_by_role('link', name='生成行为树', exact=True).click()
    panel = page.get_by_test_id('robotics-task')
    expect(panel.get_by_test_id('task-current-version')).to_have_count(1)
    expect(panel.get_by_test_id('task-current-version')).to_contain_text('版本 1')
    for label in ['操作者署名', '操作理由', '工作头', '编辑底本', '修改依据']:
        assert panel.get_by_label(label, exact=True).count() == 0
    for name in ['知识维护', '证据管理', '版本比较', '重建合并', '审核发布']:
        assert panel.get_by_role('button', name=name, exact=True).count() == 0
    current = submit_task(page, panel)
    assert {g['field'] for g in current['gaps'] if g['kind'] == 'mapping'} == {
        'hasPerceptionProfile', 'hasPlacementProfile'}
    assert panel.get_by_label('曝光时间', exact=True).count() == 0
    assert panel.get_by_role('link', name='下载行为树 XML', exact=True).count() == 0
    final = finish(page, panel, current)
    assert final['session_id'] == current['session_id'] and final['version_id'] != original_ref['version_id']
    raw = download(page, site, panel, final)
    assert AssetStore(site.root).read_payload(**original_ref) == before
    assert len(calls) == 1
    page.reload()
    expect(page.get_by_test_id('robotics-task')).to_have_attribute('data-session-id', final['session_id'])
    assert download(page, site, page.get_by_test_id('robotics-task'), final) == raw
    site.restart()
    page.reload()
    expect(page.get_by_test_id('task-current-version')).to_contain_text('版本 ' + str(final['version_number']))
    assert download(page, site, page.get_by_test_id('robotics-task'), final) == raw
    assert len(calls) == 1
    assert not [r for r in site.evidence['requests'] if any(x in r['url'] for x in ('/publish', '/reviews', '/demo'))]
    done(site)


@pytest.mark.parametrize('width', [1440, 390])
def test_missing_parameter_has_no_guessed_default_and_invalid_value_stays_editable(live, width):
    site, page, _ = live
    ref = site.submit()['output_asset_ref']
    assets = AssetStore(site.root)
    graph = Graph().parse(data=assets.read_payload(**ref), format='xml')
    graph.set((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    graph.set((S.Slot_B_01, S.hasPlacementProfile, S.Placement_Current))
    graph.remove((S.Perception_Current, S.exposureTime, None))
    base = assets.get_version(**ref)
    version = assets.add_version(ref['ontology_id'], graph.serialize(format='xml', encoding='utf-8'),
        payload_kind='robotics_rdf', parent_version_id=ref['version_id'],
        source_asset_ids=base['source_asset_ids'], metadata=base['metadata'])
    page.set_viewport_size({'width': width, 'height': 950})
    panel = open_task(page, site, version)
    current = submit_task(page, panel)
    assert [g['field'] for g in current['gaps'] if g['kind'] == 'parameter'] == ['exposureTime']
    field = panel.get_by_label('曝光时间', exact=True)
    expect(field).to_have_value('')
    assert panel.get_by_label('识别阈值', exact=True).count() == 0
    field.fill('-1')
    panel.get_by_role('button', name='保存并继续', exact=True).click()
    expect(panel.get_by_role('alert')).to_be_visible()
    expect(field).to_have_value('-1')
    assert len(assets.list_versions(ref['ontology_id'])) == 2
    assert panel.get_by_role('link', name='下载行为树 XML', exact=True).count() == 0
    field.fill('155')
    button = panel.get_by_role('button', name='保存并继续', exact=True)
    button.focus()
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/completion')) as pending:
        page.keyboard.press('Enter')
    assert pending.value.status == 200
    after = pending.value.json()
    saved = Graph().parse(data=assets.read_payload(after['ontology_id'], after['version_id']), format='xml')
    assert str(saved.value(S.Perception_Current, S.exposureTime)) == '155'
    assert str(saved.value(S.Perception_Current, S.threshold)) == config()['Perception']['threshold']
    final = finish(page, panel, after)
    values = config()
    values['Perception']['exposure_time'] = '155'
    download(page, site, panel, final, values=values)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    done(site)


def test_unknown_configuration_can_be_created_inline_without_user_iri(live):
    site, page, _ = live
    ref = site.submit()['output_asset_ref']
    panel = open_task(page, site, ref)
    state = submit_task(page, panel)
    item = next(g for g in state['gaps'] if g['field'] == 'hasPerceptionProfile')
    card = panel.locator('[data-gap-id="' + item['id'] + '"]')
    card.get_by_role('button', name='新建关联对象', exact=True).click()
    card.get_by_label('对象名称', exact=True).fill('页内合成感知配置')
    assert card.get_by_label('IRI', exact=True).count() == 0
    after = save_gaps(page, panel)
    graph = Graph().parse(data=AssetStore(site.root).read_payload(after['ontology_id'], after['version_id']), format='xml')
    target = graph.value(S.PartType_A, S.hasPerceptionProfile)
    assert target != S.Perception_Current and (target, RDF.type, S.PerceptionProfile) in graph
    assert {g['field'] for g in after['gaps'] if g['kind'] == 'parameter'} >= {
        'objectName', 'modelName', 'threshold', 'exposureTime'}
    for label in ['识别阈值', '曝光时间']:
        expect(panel.get_by_label(label, exact=True)).to_have_value('')
    done(site)


def test_optional_notes_do_not_block_generation_or_confirm_applicability(live):
    site, page, _ = live
    panel = open_task(page, site, site.submit()['output_asset_ref'])
    state = submit_task(page, panel)
    for _ in range(5):
        if state['status'] == 'generated': break
        fill_gaps(panel, state)
        state = save_gaps(page, panel)
    assert state['status'] == 'generated' and state['task_readiness'] == 'not_verified'
    expect(panel.get_by_role('link', name='下载行为树 XML', exact=True)).to_be_visible()
    assert panel.get_by_label('有效期', exact=True).count() == 0
    panel.get_by_text('适配记录（可选）', exact=True).click()
    panel.get_by_label('抓取备注', exact=True).fill(BASIS)
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/notes')) as pending:
        panel.get_by_role('button', name='保存记录', exact=True).click()
    assert pending.value.status == 200
    after = pending.value.json()
    assert after['version_id'] == state['version_id']
    assert after['task_readiness'] == 'not_verified' and after['status'] == 'generated'
    assert after['adaptation_notes'][-1]['text'] == BASIS
    page.reload()
    expect(panel.get_by_role('link', name='下载行为树 XML', exact=True)).to_be_visible()
    panel.get_by_text('适配记录（可选）', exact=True).click()
    expect(panel.get_by_label('抓取备注', exact=True)).to_have_value(BASIS)
    done(site)


def test_model_failure_is_visible_without_fake_task_or_xml(live):
    site, page, _ = live
    site.stop()
    site.env['DATAMIND_LLM_KEY'] = ''
    # Keep this case offline: no scripted credentials at build or task admission.
    LiveSite.start(site)
    panel = open_task(page, site, site.submit()['output_asset_ref'], enable_model=False)
    expect(panel.get_by_test_id('task-model-status')).to_contain_text('未配置')
    expect(panel.get_by_role('button', name='生成行为树', exact=True)).to_be_disabled()
    assert panel.get_by_role('link', name='下载行为树 XML', exact=True).count() == 0
    done(site)


def test_import_demo_is_explicit_and_real_asset_cannot_use_demo_switch_to_fill_gaps(live, tmp_path):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']
    panel = open_task(page, site, ref)
    state = submit_task(page, panel)
    assert state['mode'] == 'real' and state['status'] == 'needs_information'
    original = AssetStore(site.root).read_payload(**ref)
    panel.get_by_role('button', name='打开独立演示', exact=True).click()
    expect(panel).to_have_attribute('data-mode', 'demo')
    assert panel.get_attribute('data-ontology-id') != ref['ontology_id']
    assert AssetStore(site.root).read_payload(**ref) == original
    assert len(calls) == 1
    page.goto(site.url + '/#library')
    page.get_by_role('button', name='导入机器人 OWL', exact=True).click()
    page.get_by_label('OWL 文件', exact=True).set_input_files(
        {'name': 'candidate.owl', 'mimeType': 'application/rdf+xml', 'buffer': fresh_payload('real')})
    page.get_by_label('本体名称', exact=True).fill('可选导入测试')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/robotics/imports')) as pending:
        page.get_by_role('button', name='导入', exact=True).click()
    assert pending.value.status == 201
    imported = pending.value.json()
    expect(page.get_by_test_id('robotics-task')).to_have_attribute('data-version-id', imported['version_id'])
    done(site)


def test_new_task_during_slow_model_response_cannot_restore_old_form(live):
    site, page, _ = live
    entered, release = threading.Event(), threading.Event()
    with model_server({'entered': entered, 'release': release}) as (url, calls):
        site.stop()
        site.model_url = url
        site.start()
        panel = open_task(page, site, site.submit()['output_asset_ref'])
        original_sid = panel.get_attribute('data-session-id')
        panel.get_by_label('任务描述', exact=True).fill(TEXT)
        panel.get_by_role('button', name='生成行为树', exact=True).click()
        assert entered.wait(timeout=5)
        panel.get_by_role('button', name='新任务', exact=True).click()
        expect(panel).not_to_have_attribute('data-session-id', original_sid)
        new_sid = panel.get_attribute('data-session-id')
        release.set()
        # Poll the real old session to observe completion rather than sleeping a guessed duration.
        from tests.robotics_browser_support import wait_for
        wait_for(lambda: site.get('/api/robotics/tasks/' + original_sid)['status'] != 'running')
        page.reload()
        expect(panel).to_have_attribute('data-session-id', new_sid)
        assert panel.get_by_test_id('task-gap').count() == 0
        assert panel.get_by_role('link', name='下载行为树 XML', exact=True).count() == 0
        assert len(calls) == 1
    done(site)


def test_model_setting_bt_extract_is_saved_and_used_without_exposing_secret(live):
    site, page, calls = live
    page.goto(site.url + '/#enginecfg')
    field = page.locator('#eg_tm_bt_extract')
    expect(field).to_be_visible()
    field.fill('task-specific-model')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/engine/config')) as pending:
        field.locator('xpath=ancestor::div[contains(@class,"step")]').get_by_role('button', name='保存', exact=True).click()
    assert pending.value.status == 200
    assert site.get('/api/engine/config')['task_models']['bt_extract'] == 'task-specific-model'
    panel = open_task(page, site, site.submit()['output_asset_ref'])
    submit_task(page, panel)
    assert calls[-1]['payload']['model'] == 'task-specific-model'
    assert 'local-model-test-key' not in panel.inner_text()
    done(site)


def test_legacy_data_entries_and_direct_advanced_route_remain_available(live):
    site, page, _ = live
    key = site.seed_legacy()
    for route, selector in [('graph=' + key, '#g_sel'), ('review', '#rv_sel'), ('chat', '#p_chat')]:
        page.goto(site.url + '/#' + route)
        expect(page.locator(selector)).to_be_visible()
        assert page.get_by_test_id('robotics-task').count() == 0
    ref = site.submit()['output_asset_ref']
    page.goto(site.url + '/#library?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&tab=knowledge')
    expect(page.get_by_test_id('robotics-review')).to_be_visible()
    done(site)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.check-results/robotics-task-browser')
    parser.add_argument('--case')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_TASK_TEST_OUTPUT'] = str(args.output)
    command = [str(Path(__file__).resolve()), '-q', '-ra', '--junitxml=' + str(args.output / 'junit.xml')]
    if args.case: command += ['-k', args.case]
    raise SystemExit(pytest.main(command))


def test_task_application_history_retains_two_generations_and_reopens_without_model(live):
    """DR-069: one ontology, independent tasks, discoverable fixed results after restart."""
    from tests.robotics_task_support import fixture
    site, page, calls = live
    _, version, _ = fixture(site.root, ready=True)
    site.enable_task_model()
    page.goto(site.url + '/#robotics-task')
    expect(page.locator('.nav-group').filter(has_text='本体应用').get_by_text('行为树生成', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='从一句任务，到行为树', exact=True)).to_be_visible()
    assert site.posts() == [] and calls == []
    page.get_by_label('选择机器人本体', exact=True).select_option(version['ontology_id'])
    page.get_by_role('button', name='开始新任务', exact=True).click()
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-version-id', version['version_id'])
    first = submit_task(page, panel)
    assert first['status'] == 'generated'
    panel.get_by_role('button', name='新任务', exact=True).click()
    expect(panel).to_have_attribute('data-status', 'idle')
    second = submit_task(page, panel)
    assert second['status'] == 'generated' and second['session_id'] != first['session_id']
    history = page.get_by_test_id('robotics-task-history')
    expect(history.locator('[data-history-run]')).to_have_count(2)
    expect(history).to_contain_text(first['asset_name'])
    before_calls, before_posts = len(calls), len(site.posts())
    history.locator('[data-history-run="' + first['run_id'] + '"]').click()
    result = page.get_by_test_id('robotics-generation-history')
    expect(result).to_have_attribute('data-run-id', first['run_id'])
    expect(result.get_by_test_id('task-tree')).to_be_visible()
    expect(result).to_contain_text(first['asset_name'])
    with page.expect_download() as download:
        result.get_by_role('link', name='下载行为树 XML', exact=True).click()
    assert Path(download.value.path()).read_bytes() == assert_generated(site.root, first)
    route = page.url
    site.restart()
    page.goto(route)
    expect(page.get_by_test_id('robotics-generation-history')).to_have_attribute('data-run-id', first['run_id'])
    assert len(calls) == before_calls and len(site.posts()) == before_posts
    page.get_by_label('搜索生成记录', exact=True).fill('不存在的记录')
    expect(page.get_by_test_id('robotics-task-history')).to_contain_text('没有匹配的记录')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#p_robotics-task').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(site.url + '/#chat')
    expect(page.locator('#chat_q')).to_be_visible()
    done(site)


def test_graph_starts_task_with_selected_ontology_version(live):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']
    site.enable_task_model()
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=relations')
    page.get_by_role('link', name='基于此本体生成行为树', exact=True).click()
    # DR-071: user-approved migration; diagnosis precedes task admission.
    report = page.get_by_test_id('application-check')
    expect(report).to_contain_text('可准备任务')
    assert report.get_attribute('data-check-id')
    assert calls == []
    page.get_by_role('link', name='继续描述任务', exact=True).click()
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-ontology-id', ref['ontology_id'])
    expect(panel).to_have_attribute('data-version-id', ref['version_id'])
    assert calls == []
    expect(page.get_by_test_id('robotics-task-history')).to_contain_text('未开始')
    done(site)
