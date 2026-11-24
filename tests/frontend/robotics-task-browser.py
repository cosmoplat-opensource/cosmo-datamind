"""T04 RED: real browser/build/OWL/completion/compiler/download; only model HTTP is scripted — DR-145 / DR-148."""
import argparse
from contextlib import contextmanager
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
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.browser_runtime import browser_executable  # noqa: E402


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
        # DR-152: create the legacy asset offline, then configure task extraction.
        # A configured build now belongs to the new TBox/ABox flow.
        if not self.model_enabled:
            self.model_enabled = True
            self.restart()


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as pw:
        executable = browser_executable()
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


@contextmanager
def slow_network(page, latency_ms):
    """Delay the real browser transport without replacing backend responses."""
    network = page.context.new_cdp_session(page)
    network.send('Network.enable')
    network.send('Network.emulateNetworkConditions', {
        'offline': False, 'latency': latency_ms, 'downloadThroughput': -1, 'uploadThroughput': -1,
    })
    try:
        yield
    finally:
        network.send('Network.emulateNetworkConditions', {
            'offline': False, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1,
        })
        network.detach()


def test_task_entry_waits_for_actual_fixed_version_on_slow_network(live):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']
    site.enable_task_model()
    page.goto(site.url + '/#library')
    page.wait_for_load_state('networkidle')
    with slow_network(page, 6000):
        panel = open_task(page, site, ref)
    expect(panel).to_have_attribute('data-status', 'idle')
    state = site.get('/api/robotics/tasks/' + panel.get_attribute('data-session-id'))
    assert state['ontology_id'] == ref['ontology_id'] and state['version_id'] == ref['version_id']
    assert state['status'] == 'idle' and state['candidate'] is None and calls == []
    assert not any(r['url'].endswith(('/messages', '/completion', '/generate')) for r in site.evidence['requests'])
    done(site)


@pytest.mark.parametrize('phase', ['headers', 'body', 'navigation'])
def test_xml_download_deadline_cancellation_and_explicit_retry(live, phase):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; panel = open_task(page, site, ref)
    current = finish(page, panel, submit_task(page, panel))
    before = site.get('/api/robotics/tasks/' + current['session_id'])
    versions = AssetStore(site.root).list_versions(ref['ontology_id'])
    link = panel.get_by_role('link', name='下载行为树 XML', exact=True)
    href = link.get_attribute('href')
    page.wait_for_load_state('networkidle')
    page.clock.install()
    downloaded = []
    page.on('download', lambda item: downloaded.append(item))
    # Delay only the download transport; generation and the final retry use real HTTP/XML.
    page.evaluate('''({href,phase})=>{
      const original=window.fetch.bind(window);window.__downloadOriginal=original;
      const probe=window.__downloadProbe={calls:0,signal:null,release:null};
      window.fetch=(url,options={})=>{
        if(new URL(url,location.href).pathname+new URL(url,location.href).search!==href)return original(url,options);
        probe.calls++;probe.signal=options.signal;
        if(phase==='headers')return new Promise(resolve=>{probe.release=()=>resolve(new Response('late'));});
        return Promise.resolve({ok:true,status:200,blob:()=>new Promise(resolve=>{
          probe.release=()=>resolve(new Blob(['late']));
        })});
      };
    }''', {'href': href, 'phase': phase})
    link.evaluate('node=>{node.click();node.click();}')
    page.wait_for_function('window.__downloadProbe.release!==null')
    if phase == 'navigation':
        page.goto(site.url + '/#library')
        expect(page.get_by_test_id('ontology-catalog')).to_be_visible()
    else:
        page.clock.fast_forward(30001)
        expect(panel.get_by_role('alert')).to_contain_text('下载超时')
        expect(link).not_to_have_attribute('aria-disabled', 'true')
    assert page.evaluate('window.__downloadProbe.signal?.aborted===true')
    assert page.evaluate('window.__downloadProbe.calls') == 1
    page.evaluate('window.__downloadProbe.release()')
    page.clock.run_for(10)
    assert downloaded == [], 'a late download must never save a file'
    page.evaluate('window.fetch=window.__downloadOriginal')
    if phase == 'navigation':
        assert page.url.endswith('#library')
        page.goto(site.url + '/#robotics-task?session=' + current['session_id'])
        expect(panel.get_by_test_id('task-tree')).to_be_visible()
    else:
        # Failed HTTP replies release controls and preserve the current result for retry.
        page.evaluate('''href=>{const original=window.fetch;window.fetch=(url,options)=>
          new URL(url,location.href).pathname+new URL(url,location.href).search===href?
          Promise.resolve(new Response(JSON.stringify({error:{message:'合成下载拒绝'}}),{status:503})):original(url,options);
        }''', href)
        link.click()
        expect(panel.get_by_role('alert')).to_contain_text('合成下载拒绝')
        expect(link).not_to_have_attribute('aria-disabled', 'true')
        page.evaluate('window.fetch=window.__downloadOriginal')
    download(page, site, panel, current)
    assert site.get('/api/robotics/tasks/' + current['session_id']) == before
    assert AssetStore(site.root).list_versions(ref['ontology_id']) == versions and len(calls) == 1
    done(site)


def test_timed_out_saved_completion_retries_same_identity_without_duplicate_version(live):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; panel = open_task(page, site, ref)
    current = submit_task(page, panel)
    fill_gaps(panel, current)
    held, payloads = [], []
    page.on('request', lambda r: payloads.append(r.post_data_json)
            if r.method == 'POST' and r.url.endswith('/completion') else None)

    def lose_reply(route):
        response = route.fetch()
        assert response.status == 200
        held.append((route.request.post_data_json, response.json()))

    page.clock.install()
    page.route('**/api/robotics/tasks/*/completion', lose_reply)
    with page.expect_request('**/api/robotics/tasks/*/completion'):
        panel.get_by_role('button', name='保存并继续', exact=True).click()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(25)
    assert len(held) == 1
    saved = held[0][1]
    versions = AssetStore(site.root).list_versions(ref['ontology_id'])
    assert saved['version_id'] != current['version_id']
    page.clock.fast_forward(30001)
    expect(panel.get_by_role('alert')).to_contain_text('请求超时')
    expect(panel.get_by_role('alert')).to_contain_text('保存结果待确认')
    expect(panel.get_by_role('alert')).not_to_contain_text('保存未成功')
    expect(panel.get_by_role('button', name='保存并继续', exact=True)).to_be_enabled()
    assert len(held) == 1 and len(calls) == 1
    page.unroute('**/api/robotics/tasks/*/completion', lose_reply)
    retried = save_gaps(page, panel)
    expect(panel).to_have_attribute('data-version-id', saved['version_id'])
    posts = [r for r in site.evidence['requests'] if r['method'] == 'POST' and r['url'].endswith('/completion')]
    assert len(posts) == 2
    assert retried['revision'] == saved['revision'] and retried['version_id'] == saved['version_id']
    assert AssetStore(site.root).list_versions(ref['ontology_id']) == versions
    assert len(calls) == 1
    assert payloads == [held[0][0], held[0][0]]
    done(site)


def open_task(page, site, ref, *, enable_model=True):
    if enable_model:
        site.enable_task_model()
    page.goto(site.url + '/#robotics-task?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_be_visible(timeout=30000)
    expect(panel).to_have_attribute('data-version-id', ref['version_id'], timeout=30000)
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
            # DR-166: confirm only after the remaining mappings/values are visible.
            if not any(g['kind'] in ('mapping', 'parameter') for g in state['gaps']):
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
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/completion'), timeout=30000) as pending:
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


def test_save_feedback_names_missing_fields_and_unchecked_knowledge_without_writes(live):
    """DR-168: empty or blank saves explain the current stage and keep drafts."""
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; panel = open_task(page, site, ref)
    current = submit_task(page, panel)
    before = site.get('/api/robotics/tasks/' + current['session_id'])
    completion_posts = []
    page.on('request', lambda r: completion_posts.append(r) if r.method == 'POST' and r.url.endswith('/completion') else None)
    panel.get_by_role('button', name='保存并继续', exact=True).click()
    alert = panel.locator('.rt-completion [role="alert"]')
    expect(alert).to_contain_text('感知配置')
    expect(alert).to_contain_text('放置配置')
    assert completion_posts == [] and site.get('/api/robotics/tasks/' + current['session_id']) == before
    for _ in range(8):
        if not any(g['kind'] in ('mapping', 'parameter') for g in current['gaps']):
            break
        fill_gaps(panel, current); current = save_gaps(page, panel)
        expect(panel).to_have_attribute('data-version-id', current['version_id'])
    checkbox = panel.get_by_role('checkbox', name='我已核对当前任务知识', exact=True)
    expect(checkbox).to_be_visible(); expect(checkbox).not_to_be_checked()
    fill_gaps(panel, {**current, 'gaps': [g for g in current['gaps'] if g['kind'] != 'confirmation']})
    # Save any independent evidence first; partial saves must remain supported.
    if any(g['kind'] == 'applicability' for g in current['gaps']):
        current = save_gaps(page, panel)
        expect(panel).to_have_attribute('data-version-id', current['version_id'])
    assert current['gaps'] and all(g['kind'] == 'confirmation' for g in current['gaps'])
    before = site.get('/api/robotics/tasks/' + current['session_id']); count = len(completion_posts)
    panel.get_by_role('button', name='保存并继续', exact=True).click()
    expect(alert).to_contain_text('请先勾选“我已核对当前任务知识”')
    assert len(completion_posts) == count and site.get('/api/robotics/tasks/' + current['session_id']) == before
    expect(panel.get_by_role('button', name='保存并继续', exact=True)).to_be_enabled()
    checkbox.check(); current = save_gaps(page, panel)
    assert current['status'] == 'generated' and len(calls) == 1
    download(page, site, panel, current); done(site)


def test_task_knowledge_is_confirmed_once_after_all_mapping_and_parameter_edits(live):
    """DR-166: never confirm unseen dependencies or ask twice for known edits."""
    site, page, calls = live
    ref = site.submit()['output_asset_ref']
    original = AssetStore(site.root).read_payload(**ref)
    panel = open_task(page, site, ref)
    current = submit_task(page, panel)
    confirmations = 0
    for _ in range(8):
        if current['status'] == 'generated':
            break
        assert current['status'] == 'needs_information' and current['gaps']
        mutable = any(g['kind'] in ('mapping', 'parameter') for g in current['gaps'])
        checkbox = panel.get_by_role('checkbox', name='我已核对当前任务知识', exact=True)
        if mutable:
            expect(checkbox).to_have_count(0)
        elif any(g['kind'] == 'confirmation' for g in current['gaps']):
            expect(checkbox).not_to_be_checked()
            checkbox.check(); confirmations += 1
        fill_gaps(panel, {**current, 'gaps': [g for g in current['gaps'] if g['kind'] != 'confirmation']})
        current = save_gaps(page, panel)
    assert current['status'] == 'generated' and confirmations == 1
    assert len(calls) == 1 and AssetStore(site.root).read_payload(**ref) == original
    download(page, site, panel, current)
    done(site)


def test_inline_application_check_back_keeps_task_scope_draft_and_enabled_repair(live):
    """DR-167: return refresh also handles an existing task's embedded report."""
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; panel = open_task(page, site, ref)
    current = submit_task(page, panel)
    body = {'entrypoint': 'robotics.generate_behavior_tree',
        'asset_refs': [{k: current[k] for k in ('ontology_id', 'version_id')}],
        'context': {'mode': current['mode'], 'task_ref': {'session_id': current['session_id']}},
        'request_id': 'inline-navigation-check'}
    response = page.request.post(site.url + '/api/application-checks', data=body)
    assert response.status == 201, response.text()
    cid = response.json()['report']['check_id']
    page.goto(site.url + '/#robotics-task?asset=' + current['ontology_id'] + '&version=' + current['version_id'] +
        '&session=' + current['session_id'] + '&check=' + cid)
    expect(panel).to_have_attribute('data-session-id', current['session_id'])
    mapping = next(g for g in current['gaps'] if g['field'] == 'hasPerceptionProfile')
    select = panel.locator('[data-target="' + mapping['id'] + '"]')
    select.select_option(str(S.Perception_Current))
    check = page.get_by_test_id('application-check')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/repairs')) as pending:
        check.get_by_role('button', name='补齐任务信息', exact=True).click()
    assert pending.value.status == 200
    page.wait_for_function('!new URLSearchParams(location.hash.split("?")[1]).has("check")')
    expect(panel).to_have_attribute('data-session-id', current['session_id'])
    page.go_back()
    expect(check.get_by_role('button', name='补齐任务信息', exact=True)).to_be_enabled()
    new_id = check.get_attribute('data-check-id'); assert new_id != cid
    new = page.request.get(site.url + '/api/application-checks/' + new_id).json()['report']
    assert new['task_ref']['session_id'] == current['session_id'] and new['asset_refs'] == body['asset_refs']
    check.get_by_role('button', name='补齐任务信息', exact=True).click()
    page.wait_for_function('!new URLSearchParams(location.hash.split("?")[1]).has("check")')
    expect(panel).to_have_attribute('data-session-id', current['session_id'])
    expect(select).to_have_value(str(S.Perception_Current))
    assert len(calls) == 1
    done(site)


@pytest.mark.parametrize('navigation', ['back', 'recheck', 'direct'])
def test_back_after_task_version_change_offers_current_task_instead_of_retry_loop(live, navigation):
    """DR-167: a fixed report cannot be replayed against a newer task version."""
    from domain_runs.robotics_task_service import TaskService
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; panel = open_task(page, site, ref)
    current = submit_task(page, panel)
    body = {'entrypoint': 'robotics.generate_behavior_tree',
        'asset_refs': [{k: current[k] for k in ('ontology_id', 'version_id')}],
        'context': {'mode': 'real', 'task_ref': {'session_id': current['session_id']}}, 'request_id': 'changing-task-check'}
    response = page.request.post(site.url + '/api/application-checks', data=body)
    assert response.status == 201
    cid = response.json()['report']['check_id']
    page.goto(site.url + '/#robotics-task?asset=' + current['ontology_id'] + '&version=' + current['version_id'] +
        '&session=' + current['session_id'] + '&check=' + cid)
    expect(panel).to_have_attribute('data-version-id', current['version_id'])
    expect(page.get_by_test_id('application-check').get_by_role('button', name='补齐任务信息', exact=True)).to_be_visible()
    if navigation == 'back':
        page.get_by_role('button', name='补齐任务信息', exact=True).click()
        page.wait_for_function('!new URLSearchParams(location.hash.split("?")[1]).has("check")')
    targets = {'hasPerceptionProfile': S.Perception_Current, 'hasPlacementProfile': S.Placement_Current}
    newer = TaskService(site.root, generation_policy='offline').complete(current['session_id'],
        expected_revision=current['revision'], request_id='another-tab-completion',
        answers=[{'gap_id': g['id'], 'target': str(targets[g['field']])} for g in current['gaps'] if g['kind'] == 'mapping'])
    assert newer['version_id'] != current['version_id']
    if navigation == 'back':
        page.go_back()
    elif navigation == 'recheck':
        page.get_by_role('button', name='补齐任务信息', exact=True).click()
        expect(page.locator('#rt_task').get_by_role('alert')).to_contain_text('重新检查')
        page.get_by_test_id('application-check').get_by_role('button', name='重新检查', exact=True).click()
    else:
        page.goto(site.url + '/#robotics-task?asset=' + current['ontology_id'] + '&version=' + current['version_id'] +
            '&session=' + current['session_id'])
    link = page.get_by_role('link', name='查看当前任务', exact=True)
    expect(link).to_be_visible()
    link.click()
    expect(panel).to_have_attribute('data-session-id', current['session_id'])
    expect(panel).to_have_attribute('data-version-id', newer['version_id'])
    assert len(calls) == 1
    done(site)


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
    expect(panel).to_have_attribute('data-version-id', original_ref['version_id'], timeout=30000)
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
    field.fill('  ')
    panel.get_by_role('button', name='保存并继续', exact=True).click()
    expect(panel.get_by_role('alert')).to_contain_text('曝光时间：请填写实际参数')
    expect(field).to_have_value('  ')
    assert len(assets.list_versions(ref['ontology_id'])) == 2
    field.fill('-1')
    panel.get_by_role('button', name='保存并继续', exact=True).click()
    expect(panel.get_by_role('alert')).to_be_visible()
    expect(field).to_have_value('-1')
    assert len(assets.list_versions(ref['ontology_id'])) == 2
    assert panel.get_by_role('link', name='下载行为树 XML', exact=True).count() == 0
    field.fill('155')
    button = panel.get_by_role('button', name='保存并继续', exact=True)
    button.focus()
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/completion'), timeout=30000) as pending:
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


@pytest.mark.parametrize('timeout_retry,response_latency_ms', [
    pytest.param(False, 0, id='False'),
    pytest.param(False, 4000, id='delayed-response'),
    pytest.param(True, 0, id='True'),
])
def test_unused_profile_is_preserved_and_task_errors_retry_current_selection(live, timeout_retry, response_latency_ms):
    """DR-170: ignored drafts stay intact; old errors retry without a modelling detour."""
    from rdflib import RDFS, Literal
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; assets = AssetStore(site.root)
    original = assets.read_payload(**ref)
    graph = Graph().parse(data=original, format='xml')
    graph.set((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    graph.set((S.Slot_B_01, S.hasPlacementProfile, S.Placement_Current))
    graph.add((S.OtherProfile, RDF.type, S.PerceptionProfile))
    graph.add((S.OtherProfile, RDFS.label, Literal('备用感知配置')))
    graph.remove((S.Perception_Current, S.exposureTime, None))
    base = assets.get_version(**ref)
    version = assets.add_version(ref['ontology_id'], graph.serialize(format='xml', encoding='utf-8'),
        payload_kind='robotics_rdf', parent_version_id=ref['version_id'],
        source_asset_ids=base['source_asset_ids'], metadata=base['metadata'])
    panel = open_task(page, site, version); current = submit_task(page, panel)
    for _ in range(4):
        if current['status'] == 'generated':
            break
        assert current['status'] == 'needs_information' and current['gaps']
        fill_gaps(panel, current); current = save_gaps(page, panel)
        expect(panel).to_have_attribute('data-version-id', current['version_id'])
    assert current['status'] == 'generated' and current['candidate']
    expect(panel.get_by_role('alert')).to_have_count(0)
    expect(panel.get_by_role('link', name='完善本体', exact=True)).to_have_count(0)
    assert (S.OtherProfile, RDF.type, S.PerceptionProfile) in Graph().parse(
        data=assets.read_payload(current['ontology_id'], current['version_id']), format='xml')
    values = config(); values['Perception']['exposure_time'] = '155'
    download(page, site, panel, current, values=values)
    # A historical response contains no structured diagnostic; do not rewrite it on read.
    import json
    import sqlite3
    legacy = {**current, 'status': 'error', 'candidate': None, 'tree': None, 'run_id': None, 'result': None, 'issues': [], 'error': {'code': 'generation_error',
        'message': '信息已保存，但本体仍未通过严格生成校验，请检查后继续。'}}
    with sqlite3.connect(site.root / 'domain.sqlite3') as conn:
        conn.execute('UPDATE robotics_task_sessions SET record=? WHERE session_id=?',
                     (json.dumps(legacy), current['session_id']))
    page.reload()
    expect(panel.get_by_role('alert')).to_contain_text('行为树未能生成')
    assert '严格生成校验' not in panel.get_by_role('alert').inner_text()
    assert site.get('/api/robotics/tasks/' + current['session_id'])['error'] == legacy['error']
    expect(panel.get_by_role('link', name='完善本体', exact=True)).to_have_count(0)
    held = []
    if timeout_retry:
        def lose_generation_reply(route):
            response = route.fetch()
            assert response.status == 200
            held.append((route.request.post_data_json, response.json()))
        page.clock.install()
        page.route('**/api/robotics/tasks/*/generate', lose_generation_reply)
        with page.expect_request('**/api/robotics/tasks/*/generate'):
            panel.get_by_role('button', name='按当前配置重试生成', exact=True).click()
        for _ in range(1200):
            if held:
                break
            page.wait_for_timeout(25)
        assert len(held) == 1
        page.clock.fast_forward(30001)
        expect(panel.get_by_role('alert')).to_contain_text('请求超时')
        expect(panel.get_by_role('button', name='按当前配置重试生成', exact=True)).to_be_enabled()
        page.unroute('**/api/robotics/tasks/*/generate', lose_generation_reply)
    network = None
    if response_latency_ms:
        # Keep the actual generation HTTP and payload; only throttle Chromium's transport.
        network = page.context.new_cdp_session(page)
        network.send('Network.enable')
        network.send('Network.emulateNetworkConditions', {
            'offline': False, 'latency': response_latency_ms,
            'downloadThroughput': -1, 'uploadThroughput': -1,
        })
    try:
        # Generation has the same 30-second request deadline as completion, not the UI action timeout.
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/generate'), timeout=30000) as retry:
            panel.get_by_role('button', name='按当前配置重试生成', exact=True).click()
    finally:
        if network:
            network.send('Network.emulateNetworkConditions', {
                'offline': False, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1,
            })
            network.detach()
    assert retry.value.status == 200
    restored = retry.value.json()
    if response_latency_ms:
        response_start = retry.value.request.timing['responseStart']
        assert response_start > 3500, 'the real response must exceed the former UI action deadline'
        site.evidence['generation_response_start_ms'] = response_start
    assert restored['status'] == 'generated' and restored['version_id'] == current['version_id']
    if timeout_retry:
        assert retry.value.request.post_data_json == held[0][0]
        assert restored['run_id'] == held[0][1]['run_id']
        assert restored['revision'] == held[0][1]['revision'] and len(calls) == 1
    download(page, site, panel, restored, values=values)
    assert assets.read_payload(**ref) == original
    done(site)


@pytest.mark.parametrize('field,label,kind,original', [
    ('hasPerceptionProfile', '感知配置', 'PerceptionProfile', str(S.Perception_Current)),
    ('hasPlacementProfile', '放置配置', 'PlacementProfile', str(S.Placement_Current)),
])
def test_switch_back_from_unfinished_configuration_generates_from_final_selection(live, field, label, kind, original):
    """DR-170: the actual configuration editor can abandon a draft without blocking the task."""
    site, page, calls = live
    ref = site.submit()['output_asset_ref']; assets = AssetStore(site.root)
    before = assets.read_payload(**ref)
    panel = open_task(page, site, ref); current = finish(page, panel, submit_task(page, panel))
    def save_configuration():
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/configurations')) as pending:
            panel.get_by_role('button', name='保存配置更换', exact=True).click()
        assert pending.value.status == 200, pending.value.text()
        state = pending.value.json(); expect(panel).to_have_attribute('data-version-id', state['version_id'])
        return state
    panel.get_by_role('button', name='更换'+label, exact=True).click()
    panel.get_by_role('button', name='新建配置', exact=True).click()
    panel.get_by_label('配置名称', exact=True).fill('未完成的临时'+label)
    current = save_configuration()
    assert current['status'] == 'needs_information' and current['candidate'] is None
    draft = next(c['profile_iri'] for c in current['configurations'] if c['field'] == field)
    if kind == 'PerceptionProfile':
        panel.get_by_label('识别对象名', exact=True).fill('partial-object')
        current = save_gaps(page, panel)
        assert current['status'] == 'needs_information' and current['candidate'] is None
    panel.get_by_role('button', name='更换'+label, exact=True).click()
    panel.get_by_label('选择配置', exact=True).select_option(original)
    current = save_configuration()
    assert all(g['entity_iri'] != draft for g in current['gaps'])
    expect(panel.locator('[data-value]')).to_have_count(0)
    current = finish(page, panel, current)
    expect(panel.get_by_role('alert')).to_have_count(0)
    expect(panel.get_by_role('link', name='完善本体', exact=True)).to_have_count(0)
    assert next(c['profile_iri'] for c in current['configurations'] if c['field'] == field) == original
    graph = Graph().parse(data=assets.read_payload(current['ontology_id'], current['version_id']), format='xml')
    from rdflib import URIRef
    assert (URIRef(draft), RDF.type, S[kind]) in graph
    assert graph.value(URIRef(draft), S.modelName) is None
    assert assets.read_payload(**ref) == before and len(calls) == 1
    download(page, site, panel, current)
    page.reload(); expect(panel.get_by_test_id('task-tree')).to_be_visible()
    assert len(calls) == 1
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
    assert '任务适配未验证' not in panel.inner_text() and '执行未验证' not in panel.inner_text()
    assert '设备侧验证' not in panel.inner_text()
    expect(panel.get_by_role('alert')).to_have_count(0)
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
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/robotics/task-demo'),
                              timeout=30000) as pending:
        panel.get_by_role('button', name='打开独立演示', exact=True).click()
    assert pending.value.status == 201, pending.value.text()
    expect(panel).to_have_attribute('data-mode', 'demo')
    assert panel.get_attribute('data-ontology-id') != ref['ontology_id']
    assert AssetStore(site.root).read_payload(**ref) == original
    assert len(calls) == 1
    page.goto(site.url + '/#library')
    page.get_by_role('button', name='导入机器人 OWL', exact=True).click()
    page.get_by_label('OWL 文件', exact=True).set_input_files(
        {'name': 'candidate.owl', 'mimeType': 'application/rdf+xml', 'buffer': fresh_payload('real')})
    page.get_by_label('本体名称', exact=True).fill('可选导入测试')
    with slow_network(page, 4000):
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/robotics/imports'), timeout=30000) as pending:
            page.get_by_role('button', name='导入', exact=True).click()
    assert pending.value.status == 201
    assert pending.value.request.timing['responseStart'] > 3500
    imported = pending.value.json()
    expect(page.get_by_test_id('robotics-task')).to_have_attribute('data-version-id', imported['version_id'], timeout=30000)
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
        expect(panel).not_to_have_attribute('data-session-id', original_sid, timeout=30000)
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
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_TASK_TEST_OUTPUT'] = str(args.output)
    # Two verbosity levels also override the repository's default quiet option.
    command = [str(Path(__file__).resolve()), '-vv', '-ra', '--tb=short', '--durations=20',
               '--junitxml=' + str(args.output / 'junit.xml')]
    # DR-163: each worker owns its browser, model server and per-test storage.
    if args.workers > 1:
        command.extend(['-n', str(args.workers), '--dist', 'worksteal'])
    if args.case: command += ['-k', args.case]
    raise SystemExit(pytest.main(command))


def test_task_application_history_retains_two_generations_and_reopens_without_model(live):
    """DR-151: one ontology, independent tasks, discoverable fixed results after restart."""
    from tests.robotics_task_support import fixture
    site, page, calls = live
    _, version, _ = fixture(site.root, ready=True)
    site.enable_task_model()
    page.goto(site.url + '/#robotics-task')
    expect(page.locator('.nav-group').filter(has_text='本体应用').get_by_text('行为树生成', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='从一句任务，到行为树', exact=True)).to_be_visible()
    assert site.posts() == [] and calls == []
    page.get_by_label('选择机器人本体', exact=True).select_option(version['ontology_id'])
    panel = page.get_by_test_id('robotics-task')
    with slow_network(page, 6000):
        page.get_by_role('button', name='开始新任务', exact=True).click()
        expect(panel).to_have_attribute('data-version-id', version['version_id'], timeout=30000)
    first = submit_task(page, panel)
    assert first['status'] == 'generated'
    panel.get_by_role('button', name='新任务', exact=True).click()
    expect(panel).to_have_attribute('data-status', 'idle', timeout=30000)
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


@pytest.mark.parametrize('mode', ['demo', 'real'])
def test_interactive_graph_task_keeps_fixed_version_mode(live, mode):
    from domain_runs.robotics_task_demo import packaged_demo

    site, page, calls = live
    version = packaged_demo(site.root) if mode == 'demo' else site.submit()['output_asset_ref']
    ref = {key: version[key] for key in ('ontology_id', 'version_id')}
    site.enable_task_model()
    path = f"/#robotics-view?asset={ref['ontology_id']}&version={ref['version_id']}&view=interactive-ontology-graph"
    with page.expect_response(lambda r: r.url.endswith('/views/interactive-ontology-graph'), timeout=30000) as graph:
        page.goto(site.url + path)
    assert graph.value.ok and graph.value.json()['mode'] == mode
    action = page.get_by_role('link', name='基于此本体生成行为树', exact=True)
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/application-checks'),
                              timeout=30000) as checked:
        action.click(timeout=30000)
    response = checked.value
    assert response.ok, response.text()
    report = response.json()['report']
    assert response.request.post_data_json['context']['mode'] == mode
    assert report['context']['mode'] == mode and report['asset_refs'] == [ref]
    assert calls == []
    done(site)


def test_graph_starts_task_with_selected_ontology_version(live):
    site, page, calls = live
    ref = site.submit()['output_asset_ref']
    site.enable_task_model()
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=relations')
    page.get_by_role('link', name='基于此本体生成行为树', exact=True).click()
    # DR-155: retain preflight, enter directly, and expose the bound check in the task page.
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-ontology-id', ref['ontology_id'], timeout=30000)
    expect(panel).to_have_attribute('data-version-id', ref['version_id'], timeout=30000)
    expect(panel).to_contain_text('可准备任务')
    expect(panel).to_have_attribute('data-check-id', re.compile('^check_'))
    expect(page.get_by_role('link', name='继续描述任务', exact=True)).to_have_count(0)
    assert calls == []
    expect(page.get_by_test_id('robotics-task-history')).to_contain_text('未开始')
    done(site)
