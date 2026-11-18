"""Real HTTP/Chromium detail-to-task navigation and existing result access — DR-155 / T04 RED."""
import argparse
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from playwright.sync_api import expect, sync_playwright

from domain_runs import RunStore
from domain_runs.robotics_application_service import ApplicationService
from ontology_assets import AssetStore
from ontology_assets.robotics_application import ApplicationAssets
from ontology_assets.robotics_design import DesignAssets
from ontology_assets.robotics_offline import OfflineAssets
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.ontology_detail_integration_support import (
    check_records, clone_seed, create_check, detail_hash, prepare_seed, stale_check,
    task_hash, task_records,
)
from tests.robotics_design_browser_support import DesignSite
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.browser_runtime import browser_executable  # noqa: E402


@pytest.fixture(scope='module')
def detail_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('detail-browser-seed') / 'assets'
    return root, prepare_seed(root)


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        executable = browser_executable()
        instance = playwright.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def live(tmp_path, request, browser, detail_seed):
    output = Path(os.environ.get('DATAMIND_DETAIL_TEST_OUTPUT', str(ROOT / '.check-results/ir027-t04/browser')))
    output = output / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    site = DesignSite(tmp_path / 'site', output, prepare=lambda target: clone_seed(detail_seed, target))
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page(); page.set_default_timeout(4000); page.set_default_navigation_timeout(20000)
    site.observe(page)
    try:
        yield site, page, site.prepared
    finally:
        try:
            page.screenshot(path=str(output / 'final.png'), full_page=True)
        finally:
            context.close(); site.close()


def open_detail(site, page, record):
    page.goto(site.url + '/' + detail_hash(record))
    panel = page.get_by_test_id('robotics-version')
    expect(panel).to_have_attribute('data-version-id', record['version_id'], timeout=15000)
    expect(panel.get_by_role('tab', name='概览', exact=True)).to_be_visible(timeout=15000)
    return panel


def posts(site, suffix):
    return [row for row in site.evidence['requests'] if row['method'] == 'POST' and row['url'].endswith(suffix)]


def start_check(site, page):
    with page.expect_response(lambda response: response.request.method == 'POST'
                             and response.url == site.url + '/api/application-checks', timeout=15000) as response:
        page.get_by_role('link', name='生成行为树', exact=True).click()
    assert response.value.status in (200, 201), response.value.text()[:1000]
    return response.value.json()


def assert_dialog(page, record):
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-version-id', record['version_id'], timeout=5000)
    expect(panel.get_by_label('任务描述', exact=True)).to_be_visible()
    expect(panel).to_contain_text('可准备任务')
    expect(panel).to_contain_text('执行未验证')
    expect(page.get_by_role('link', name='继续描述任务', exact=True)).to_have_count(0)
    return panel


def done(site):
    assert site.evidence['page_errors'] == [], site.evidence['page_errors']
    site.evidence['assertions_completed'] = True


def wait_held(page, routes):
    deadline = time.monotonic() + 5
    while not routes and time.monotonic() < deadline:
        page.wait_for_timeout(25)  # Pump real browser network events while awaiting the controlled request.
    assert routes, 'The requested task/check must reach the real HTTP boundary.'


@pytest.mark.parametrize('kind', ['legacy', 'design', 'current_application'])
@pytest.mark.parametrize('width', [1440, 390])
def test_real_fixed_detail_tabs_are_readonly_and_fit_the_full_application_shell(live, kind, width):
    site, page, info = live; page.set_viewport_size({'width': width, 'height': 1000 if width > 600 else 844})
    before = db_rows(site.root), blob_bytes(site.root)
    panel = open_detail(site, page, info[kind])
    for name in ('概览', '类别与关系', '实例', '来源与版本'):
        page.get_by_role('tab', name=name, exact=True).click()
        expect(page.get_by_role('tabpanel', name=name, exact=True)).to_be_visible()
        geometry = panel.evaluate('node=>[node.scrollWidth-node.clientWidth,document.documentElement.scrollWidth-innerWidth]')
        assert max(geometry) <= 1, (kind, width, name, geometry)
    assert not posts(site, '/api/application-checks') and not posts(site, '/api/robotics/tasks')
    assert (db_rows(site.root), blob_bytes(site.root)) == before
    for name in ('删除本体', '恢复本体'):
        expect(page.get_by_role('button', name=name, exact=True)).to_have_count(0)
    done(site)


@pytest.mark.parametrize('kind', ['legacy', 'application', 'current_application'])
def test_successful_fresh_check_enters_task_dialog_without_a_success_hop(live, kind):
    site, page, info = live; record = info[kind]
    original = AssetStore(site.root).read_payload(**record)
    sessions = task_records(site.root); reports = check_records(site.root)
    open_detail(site, page, record); response = start_check(site, page)
    assert response['freshness'] == 'current' and response['report']['preparation_status'] == 'available'
    panel = assert_dialog(page, record)
    new = set(task_records(site.root)) - set(sessions)
    assert new == {panel.get_attribute('data-session-id')}
    assert len(check_records(site.root)) == len(reports) + 1
    assert len(posts(site, '/api/application-checks')) == len(posts(site, '/api/robotics/tasks')) == 1
    assert AssetStore(site.root).read_payload(**record) == original
    done(site)


def test_duplicate_entry_clicks_and_refresh_keep_one_check_and_one_task(live):
    site, page, info = live; record = info['legacy']
    sessions, reports = task_records(site.root), check_records(site.root)
    open_detail(site, page, record)
    page.get_by_role('link', name='生成行为树', exact=True).evaluate('link=>{link.click();link.click();}')
    panel = assert_dialog(page, record); sid = panel.get_attribute('data-session-id')
    panel.get_by_label('任务描述', exact=True).fill('暂未提交的测试任务文本')
    page.reload(); panel = assert_dialog(page, record)
    expect(panel).to_have_attribute('data-session-id', sid)
    assert set(task_records(site.root)) - set(sessions) == {sid}
    assert len(check_records(site.root)) == len(reports) + 1
    assert len(posts(site, '/api/application-checks')) == len(posts(site, '/api/robotics/tasks')) == 1
    done(site)


def test_reload_after_task_admission_before_response_does_not_create_another_task(live):
    site, page, info = live; record = info['legacy']; held = []
    sessions, reports = task_records(site.root), check_records(site.root)

    def intercept(route):
        if route.request.method == 'POST' and not held:
            response = route.fetch()
            assert response.status == 201
            held.append((route, response.json()))
        else:
            route.continue_()

    page.route('**/api/robotics/tasks', intercept)
    open_detail(site, page, record); start_check(site, page)
    # This is a real accepted server request with a deliberately undelivered reply.
    wait_held(page, held)
    admitted = held[0][1]
    page.reload(); panel = assert_dialog(page, record)
    expect(panel).to_have_attribute('data-session-id', admitted['session_id'])
    assert set(task_records(site.root)) - set(sessions) == {admitted['session_id']}
    assert len(check_records(site.root)) == len(reports) + 1
    done(site)


def test_existing_task_recheck_returns_to_same_session_with_unsent_input(live):
    site, page, info = live; task = info['legacy_task']
    response = create_check(site.root, task, task=task)
    before = task_records(site.root)
    page.goto(site.url + '/' + task_hash(task, session=task['session_id']))
    panel = page.get_by_test_id('robotics-task')
    expect(panel).to_have_attribute('data-session-id', task['session_id'])
    text = '尚未提交的新任务补充：保留这一段输入'
    panel.get_by_label('任务描述', exact=True).fill(text)
    page.evaluate('(hash)=>{location.hash=hash;}', task_hash(task, check=response['report']['check_id'], task=task['session_id']))
    panel = assert_dialog(page, task)
    expect(panel).to_have_attribute('data-session-id', task['session_id'])
    expect(panel.get_by_label('任务描述', exact=True)).to_have_value(text)
    assert task_records(site.root) == before and not posts(site, '/api/robotics/tasks')
    done(site)


def test_missing_schema_stays_in_diagnosis_and_revision_targets_original_design(live):
    site, page, info = live; record = info['blocked']
    original = AssetStore(site.root).read_payload(**record); before_tasks = task_records(site.root)
    open_detail(site, page, record); response = start_check(site, page)
    report = page.get_by_test_id('application-check')
    expect(report).to_contain_text('ParameterBinding')
    expect(report.locator('[data-action-id="robotics.revise_tbox"]')).to_be_enabled()
    expect(report.locator('[data-action-id="robotics.adapt_execution"]')).to_be_enabled()
    assert response['report']['preparation_status'] == 'blocked' and not posts(site, '/api/robotics/tasks')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/repairs')) as repair:
        report.locator('[data-action-id="robotics.revise_tbox"]').click()
    body = repair.value.json()
    assert repair.value.status == 200 and body['return_context']['asset_refs'] == [record]
    expect(page.get_by_test_id('robotics-design')).to_have_attribute('data-session-id', info['blocked_session'])
    assert response['report']['check_id'] in page.url
    assert AssetStore(site.root).read_payload(**record) == original and task_records(site.root) == before_tasks
    done(site)


def test_mapping_repair_prepares_correspondence_for_same_version_without_revising_schema(live):
    site, page, info = live; record = info['blocked']
    original = AssetStore(site.root).read_payload(**record)
    versions = AssetStore(site.root).list_versions(record['ontology_id'])
    open_detail(site, page, record); response = start_check(site, page)
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/repairs')) as repair:
        page.locator('[data-action-id="robotics.adapt_execution"]').click()
    body = repair.value.json(); assert repair.value.status == 200
    expect(page.get_by_test_id('robotics-application-adaptation')).to_have_attribute('data-adaptation-id', body['target']['resource_id'])
    state = ApplicationService(site.root).get(body['target']['resource_id'])
    assert state['asset_ref'] == record and state['status'] == 'needs_tbox_revision' and state['run_id'] is None
    assert body['return_context']['asset_refs'] == response['report']['asset_refs']
    assert AssetStore(site.root).read_payload(**record) == original
    assert AssetStore(site.root).list_versions(record['ontology_id']) == versions
    done(site)


def test_stale_preparation_report_remains_diagnostic_with_recheck_and_no_auto_entry(live):
    site, page, info = live
    stale = stale_check(site.root, info); record = info['legacy']; before = task_records(site.root)
    page.goto(site.url + '/' + task_hash(record, check=stale['report']['check_id'], task=info['legacy_task']['session_id']))
    panel = page.get_by_test_id('application-check')
    expect(panel).to_contain_text('检查已过期')
    expect(panel.get_by_role('button', name='重新检查', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='继续描述任务', exact=True)).to_have_count(0)
    assert task_records(site.root) == before and not posts(site, '/api/robotics/tasks')
    done(site)


def test_lost_check_response_retries_same_admission_and_enters_only_one_task(live):
    site, page, info = live; record = info['legacy']; admitted = []
    sessions, reports = task_records(site.root), check_records(site.root)

    def interrupt(route):
        if route.request.method == 'POST' and not admitted:
            response = route.fetch(); assert response.status == 201
            admitted.append(response.json()['report']['check_id']); route.abort('failed')
        else:
            route.continue_()

    page.route('**/api/application-checks', interrupt)
    open_detail(site, page, record)
    page.get_by_role('link', name='生成行为树', exact=True).click()
    retry = page.get_by_role('button', name='重试检查', exact=True)
    expect(retry).to_be_visible()
    assert task_records(site.root) == sessions
    retry.click(); panel = assert_dialog(page, record)
    assert set(task_records(site.root)) - set(sessions) == {panel.get_attribute('data-session-id')}
    added = check_records(site.root)[len(reports):]
    assert len(added) == 1 and added[0]['check_id'] == admitted[0]
    done(site)


def test_late_check_response_cannot_navigate_away_from_another_fixed_version(live):
    site, page, info = live; held = []

    def delay(route):
        if route.request.method == 'POST':
            held.append(route)
        else:
            route.continue_()

    page.route('**/api/application-checks', delay)
    open_detail(site, page, info['legacy'])
    page.get_by_role('link', name='生成行为树', exact=True).click()
    page.wait_for_url(re.compile('.*robotics-task.*'))
    # Let the browser issue the real fetch; no replacement application response is supplied.
    wait_held(page, held)
    page.evaluate('(hash)=>{location.hash=hash;}', detail_hash(info['blocked']))
    expect(page.get_by_test_id('robotics-version')).to_have_attribute('data-version-id', info['blocked']['version_id'])
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/application-checks')):
        held[0].continue_()
    page.wait_for_load_state('networkidle')
    expect(page).to_have_url(site.url + '/' + detail_hash(info['blocked']))
    assert not posts(site, '/api/robotics/tasks')
    done(site)


@pytest.mark.parametrize('state', ['current', 'stale'])
def test_detail_omits_check_link_and_saved_report_remains_readonly(live, state):
    site, page, info = live
    response = stale_check(site.root, info) if state == 'stale' else create_check(site.root, info['legacy'])
    before = db_rows(site.root), blob_bytes(site.root)
    open_detail(site, page, info['legacy']); page.get_by_role('tab', name='概览', exact=True).click()
    panel = page.get_by_role('tabpanel', name='概览', exact=True)
    expect(panel).to_contain_text('检查已过期' if state == 'stale' else '可准备任务')
    expect(page.get_by_role('link', name='查看检查详情', exact=True)).to_have_count(0)
    page.goto(site.url + '/' + task_hash(info['legacy'], check=response['report']['check_id'], readonly=1))
    expect(page.get_by_test_id('application-check')).to_have_attribute('data-check-id', response['report']['check_id'])
    assert (db_rows(site.root), blob_bytes(site.root)) == before
    assert not posts(site, '/api/application-checks') and not posts(site, '/api/robotics/tasks')
    done(site)


@pytest.mark.parametrize('kind', ['legacy_task', 'application_task'])
def test_detail_omits_history_link_and_existing_history_remains_readonly(live, kind):
    site, page, info = live; task = info[kind]
    record = {key: task[key] for key in ('ontology_id', 'version_id')}
    expected = RunStore(site.root).read_artifact(**task['candidate'])
    before = db_rows(site.root), blob_bytes(site.root)
    open_detail(site, page, record); page.get_by_role('tab', name='概览', exact=True).click()
    expect(page.get_by_role('link', name='查看任务与生成记录', exact=True)).to_have_count(0)
    page.goto(site.url + '/' + task_hash(record, history=1))
    link = page.locator('[data-history-run="' + task['run_id'] + '"]')
    expect(link).to_be_visible(timeout=15000)
    assert task['version_id'] in link.get_attribute('href') and task['session_id'] in link.get_attribute('href')
    link.click()
    panel = page.get_by_test_id('robotics-generation-history')
    expect(panel).to_have_attribute('data-run-id', task['run_id'], timeout=15000)
    with page.expect_download() as download:
        panel.get_by_role('link', name='下载行为树 XML', exact=True).click()
    path = site.output / download.value.suggested_filename; download.value.save_as(path)
    assert path.read_bytes() == expected
    assert (db_rows(site.root), blob_bytes(site.root)) == before
    assert not posts(site, '/api/robotics/tasks') and not posts(site, '/api/application-checks')
    done(site)


@pytest.mark.parametrize('kind', ['legacy', 'design', 'application'])
def test_browser_downloads_original_fixed_json_and_owl_instead_of_latest(live, kind):
    site, page, info = live; record = info[kind]
    reader = OfflineAssets(site.root) if kind == 'legacy' else DesignAssets(site.root) if kind == 'design' else ApplicationAssets(site.root)
    expected = {'下载完整 JSON': reader.export_json(**record), '下载 OWL': AssetStore(site.root).read_payload(**record)}
    before = db_rows(site.root), blob_bytes(site.root)
    panel = open_detail(site, page, record)
    actions = panel.locator('.ra-detail-actions')
    assert actions.get_by_role('link').all_text_contents() == ['生成行为树 →', '打开图谱', '下载完整 JSON', '下载 OWL']
    expect(actions.get_by_role('button', name='移到回收站', exact=True)).to_have_count(1)
    assert actions.get_by_role('button').count() == 1
    expect(panel.get_by_role('link', name='维护知识', exact=True)).to_have_count(0)
    for name, raw in expected.items():
        with page.expect_download() as download:
            page.get_by_role('link', name=name, exact=True).click()
        path = site.output / download.value.suggested_filename; download.value.save_as(path)
        assert path.read_bytes() == raw
    assert (db_rows(site.root), blob_bytes(site.root)) == before
    done(site)


def test_detail_tabs_work_with_keyboard_and_keep_visible_panel_identity(live):
    site, page, info = live
    open_detail(site, page, info['legacy'])
    first = page.get_by_role('tab', name='概览', exact=True); first.focus(); first.press('End')
    last = page.get_by_role('tab', name='来源与版本', exact=True)
    expect(last).to_be_focused(); expect(last).to_have_attribute('aria-selected', 'true')
    last.press('Home'); expect(first).to_be_focused(); first.press('ArrowRight')
    expect(page.get_by_role('tabpanel', name='类别与关系', exact=True)).to_be_visible()
    done(site)


def test_legacy_data_graph_entry_still_opens_without_robotics_check_or_task(live):
    site, page, _ = live; key = site.seed_legacy()
    page.goto(site.url + '/#library')
    item = page.locator('[data-legacy-key="' + key + '"]')
    item.get_by_role('link', name='打开数据图谱', exact=True).click()
    expect(page).to_have_url(site.url + '/#graph=' + key)
    expect(page.locator('#p_graph')).to_be_visible()
    assert not posts(site, '/api/application-checks') and not posts(site, '/api/robotics/tasks')
    done(site)


def test_missing_fixed_version_is_an_error_without_default_or_latest_fallback(live):
    site, page, info = live
    record = {**info['design'], 'version_id': 'ver_missing'}
    page.goto(site.url + '/' + detail_hash(record))
    expect(page.get_by_test_id('robotics-version').get_by_role('alert')).to_contain_text('不存在')
    expect(page).to_have_url(site.url + '/' + detail_hash(record))
    assert not posts(site, '/api/robotics/tasks')
    done(site)


def test_pending_fixed_detail_exposes_loading_state_before_real_response(live):
    site, page, info = live; record = info['design']; held = []
    page.route('**' + '/api/ontology-assets/' + record['ontology_id'] + '/versions/' + record['version_id'], lambda route: held.append(route))
    page.goto(site.url + '/' + detail_hash(record)); wait_held(page, held)
    panel = page.get_by_test_id('robotics-version')
    expect(panel.get_by_role('status')).to_contain_text('正在读取指定版本')
    held[0].continue_()
    expect(panel).to_have_attribute('data-version-id', record['version_id'])
    assert not posts(site, '/api/robotics/tasks') and not posts(site, '/api/application-checks')
    done(site)


def test_failed_next_version_read_clears_previous_content_without_fallback(live):
    site, page, info = live
    panel = open_detail(site, page, info['legacy'])
    record = info['design']
    page.route('**' + '/api/ontology-assets/' + record['ontology_id'] + '/versions/' + record['version_id'], lambda route: route.abort('failed'))
    page.evaluate('(hash)=>{location.hash=hash;}', detail_hash(record))
    expect(panel.get_by_role('alert')).to_be_visible()
    assert panel.get_attribute('data-version-id') in (None, '')
    expect(page).to_have_url(site.url + '/' + detail_hash(record))
    assert not posts(site, '/api/robotics/tasks')
    done(site)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.check-results/ontology-detail-browser')
    options = parser.parse_args()
    os.environ['DATAMIND_DETAIL_TEST_OUTPUT'] = str(options.output.resolve())
    raise SystemExit(pytest.main([__file__, '-q', '--tb=short']))
