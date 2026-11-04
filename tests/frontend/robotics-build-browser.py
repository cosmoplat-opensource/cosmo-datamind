"""T06 RED: real Chromium + owned Gunicorn + actual robotics storage — DR-063 / DR-064 / DR-065.

Run: .venv/bin/python tests/frontend/robotics-build-browser.py --output .check-results/robotics-browser
No route interception, synthetic success response, external service or paid model.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from playwright.sync_api import expect, sync_playwright

from domain_runs import RunStore
from ontology_assets import AssetStore
from tests.build_pipeline_support import bad_xml, failure_result
from tests.robotics_browser_support import (LiveSite, choose_robotics, download_from_ui, fill_robotics,
                                            submit_from_ui, wait_run_ui, wait_version_ui)
from tests.robotics_support import SOURCE, MODELS
from tests.build_runtime_support import upload_args


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as pw:
        candidates = [os.environ.get('DATAMIND_BROWSER_EXECUTABLE'), '/usr/bin/google-chrome', '/usr/bin/chromium']
        executable = next((p for p in candidates if p and Path(p).is_file()), None)
        instance = pw.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def live(tmp_path, request, browser):
    output = Path(os.environ.get('DATAMIND_ROBOTICS_TEST_OUTPUT', str(ROOT / '.check-results/robotics-browser')))
    output = output / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    options = getattr(request, 'param', {})
    site = LiveSite(tmp_path / 'site', output, **options)
    context = page = None
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(4000)
        page.set_default_navigation_timeout(15000)
        site.observe(page)
        yield site, page
    finally:
        try:
            if page is not None:
                page.screenshot(path=str(output / 'final.png'), full_page=True)
        except Exception as exc:
            site.evidence['screenshot_error'] = str(exc)
        finally:
            try:
                if context is not None:
                    context.close()
            finally:
                site.close()


def done(site):
    assert not site.evidence['page_errors'], site.evidence['page_errors']
    site.evidence['assertions_completed'] = True


def test_real_fixture_browser_and_backend_are_available(live):
    """Infrastructure control: distinguish missing UI from missing browser/server."""
    site, page = live
    page.goto(site.url+'/#build')
    expect(page.locator('#bc_q')).to_be_visible()
    caps = page.evaluate("fetch('/api/robotics/capabilities').then(r=>r.json())")
    assert caps['enabled'] is True and caps['recipes'][0]['id'] == 'bt_xml'
    assert RunStore(site.root, read_only=True).list_runs() == []
    assert any(r['url'].endswith('/api/robotics/capabilities') for r in site.evidence['responses'])
    done(site)


def test_upload_candidate_download_refresh_and_service_restart(live):
    site, page = live
    choose_robotics(page, site)
    fill_robotics(page)
    rid = submit_from_ui(page, site, keyboard=True)
    panel = wait_run_ui(page, rid, 'completed')
    row = site.terminal(rid)
    assert row['result']['status'] == 'built'
    assert row['result']['task_readiness'] == 'not_assessed'
    assert row['result']['execution_readiness'] == 'not_verified'
    assets = AssetStore(site.root, read_only=True)
    fixed = row['build_input']['sources']
    for role, expected_bytes in [('behavior_tree', SOURCE.read_bytes()), ('node_models', MODELS.read_bytes())]:
        sid = next(s['source_asset_id'] for s in fixed if s['role'] == role)
        assert assets.read_source(sid) == expected_bytes
    seqs = panel.locator('[data-event-seq]').evaluate_all('els=>els.map(e=>Number(e.dataset.eventSeq))')
    assert seqs == sorted(set(seqs)) and seqs
    assert seqs == [e['seq'] for e in RunStore(site.root, read_only=True).events(rid)]
    assert not re.search(r'\d+\s*%', panel.inner_text()), 'No invented progress percentage'
    page.get_by_role('link', name='查看本体候选', exact=True).click()
    version = wait_version_ui(page, row['output_asset_ref'])
    text = version.inner_text()
    for words in [r'待评审', r'任务适配.{0,5}未评估', r'执行.{0,5}未验证', r'结构.{0,5}通过']:
        assert re.search(words, text), text
    for source in fixed:
        assert source['source_asset_id'] in text and source['sha256'] in text
    for kind in ['AtomicSkill', 'CompositeSkill', 'SkillInvocation']:
        expected = row['result']['validation']['ontology_counts'][kind]
        expect(version.locator(f'[data-count-kind="{kind}"]')).to_have_text(str(expected))
    for kind in ['ontology', 'validation', 'manifest']:
        download_from_ui(page, site, row, kind)
    expected_url = page.url
    page.reload()
    wait_version_ui(page, row['output_asset_ref'])
    assert page.url == expected_url and len(site.posts()) == 1
    before = RunStore(site.root, read_only=True).get_run(rid)
    site.restart()
    page.reload()
    wait_version_ui(page, row['output_asset_ref'])
    assert RunStore(site.root, read_only=True).get_run(rid) == before
    assert len(RunStore(site.root, read_only=True).list_runs()) == 1 and len(site.posts()) == 1
    download_from_ui(page, site, row, 'ontology')
    done(site)


def test_actual_model_conflict_location_report_and_explicit_retry(live):
    site, page = live
    source, models, _, _, _ = bad_xml('model_conflict')
    choose_robotics(page, site)
    fill_robotics(page, source=source, models=models)
    rid = submit_from_ui(page, site)
    panel = wait_run_ui(page, rid, 'failed')
    row = site.terminal(rid)
    text = panel.inner_text()
    for expected in ['model_conflict', 'MoveJ', 'target']:
        assert expected in text
    for issue in row['result']['issues']:
        assert issue['source_asset_id'] in text
    assert panel.get_by_role('link', name='下载 OWL', exact=True).count() == 0
    report = download_from_ui(page, site, row, 'validation')
    assert json.loads(report.read_bytes())['issues'] == row['result']['issues']
    runs = RunStore(site.root, read_only=True)
    old = runs.get_run(rid), runs.events(rid), runs.manifest(rid)
    page.reload()
    wait_run_ui(page, rid, 'failed')
    assert len(site.posts()) == 1, 'Reload cannot silently retry a failed build'
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/'+rid+'/retry')) as pending:
        page.get_by_role('button', name='重试构建', exact=True).click()
    assert pending.value.status == 202
    retry_id = pending.value.json()['run_id']
    assert retry_id != rid
    wait_run_ui(page, retry_id, 'failed')
    retried = site.terminal(retry_id)
    assert retried['retry_of'] == rid and retried['build_input'] == row['build_input']
    assert (runs.get_run(rid), runs.events(rid), runs.manifest(rid)) == old
    assert len(site.posts()) == 2
    done(site)


def test_catalog_and_jobs_keep_robotics_separate_from_legacy_data(live):
    site, page = live
    key = site.seed_legacy()
    row = site.submit()
    assert row['status'] == 'completed'
    page.goto(site.url+'/#library')
    catalog = page.get_by_test_id('ontology-catalog')
    expect(catalog).to_be_visible(timeout=2500)
    robot = catalog.locator(f'[data-ontology-id="{row["output_asset_ref"]["ontology_id"]}"]')
    expect(robot).to_contain_text('待评审')
    assert robot.get_by_text('问数', exact=True).count() == 0
    assert robot.locator('a[href^="#graph="],a[href^="#chat"]').count() == 0
    legacy = catalog.locator(f'[data-legacy-key="{key}"]')
    expect(legacy).to_contain_text('旧数据图谱')
    expect(legacy).to_contain_text('未登记版本')
    assert legacy.get_attribute('data-ontology-id') in (None, '')
    assert legacy.locator('a[href="#graph='+key+'"]').count() == 1
    page.get_by_label('本体领域', exact=True).select_option('robotics')
    expect(legacy).to_have_count(0)
    expect(robot).to_be_visible()
    page.goto(site.url+'/#jobs')
    expect(page.locator('#jobs_list')).to_be_visible()
    jobs = page.get_by_test_id('robotics-jobs')
    expect(jobs).to_be_visible()
    link = jobs.locator('a[href="#build?recipe=robotics&run='+row['run_id']+'"]')
    expect(link).to_be_visible()
    link.click()
    wait_run_ui(page, row['run_id'], 'completed')
    assert site.posts() == []
    done(site)


def test_requested_version_stays_pinned_after_newer_version_is_added(live):
    site, page = live
    row = site.submit()
    assets = AssetStore(site.root)
    ref = row['output_asset_ref']
    original = assets.get_version(**ref)
    newer = assets.add_version(ref['ontology_id'], assets.read_payload(**ref), payload_kind='robotics_rdf',
                               parent_version_id=ref['version_id'], source_asset_ids=original['source_asset_ids'])
    assert newer['version_id'] != ref['version_id']
    url = site.url+f'/#library?asset={ref["ontology_id"]}&version={ref["version_id"]}'
    page.goto(url)
    wait_version_ui(page, ref)
    download_from_ui(page, site, row, 'ontology')
    page.reload()
    wait_version_ui(page, ref)
    assert page.url == url and site.posts() == []
    done(site)


@pytest.mark.parametrize('live', [{'hold_owner': True}], indirect=True)
def test_leaving_page_stops_polling_but_queued_build_continues(live):
    site, page = live
    choose_robotics(page, site)
    fill_robotics(page)
    rid = submit_from_ui(page, site)
    wait_run_ui(page, rid, 'queued')
    assert RunStore(site.root, read_only=True).get_run(rid)['status'] == 'queued'
    # Real ownership lock holds the actual queue; there is no mocked queued response.
    page.locator('.nav[data-p="home"]').click()
    expect(page.locator('#p_home')).to_have_class(re.compile(r'\bon\b'))
    page.wait_for_timeout(250)  # Allow a request already in flight at navigation to settle.
    begin = len(site.evidence['requests'])
    page.wait_for_timeout(5500)  # Beyond the controller contract's maximum 5-second poll interval.
    assert not any('/api/domain-runs/'+rid in r['url'] for r in site.evidence['requests'][begin:])
    site.release_owner()
    row = site.terminal(rid)
    assert row['status'] == 'completed'
    page.goto(site.url+'/#build?recipe=robotics&run='+rid)
    wait_run_ui(page, rid, 'completed')
    assert len(site.posts()) == 1
    done(site)


@pytest.mark.parametrize('fragment', ['build?recipe=robotics&run=run_missing',
                                      'library?asset=ont_missing&version=ver_missing'])
def test_missing_deep_link_reports_error_without_demo_fallback(live, fragment):
    site, page = live
    page.goto(site.url+'/#'+fragment)
    scope = page.locator('#p_build' if fragment.startswith('build') else '#p_library')
    expect(scope.get_by_role('alert')).to_contain_text(re.compile(r'不存在|未找到|404'), timeout=2500)
    assert page.url.endswith('#'+fragment)
    for panel in page.get_by_test_id('robotics-version').all():
        if panel.is_visible():
            assert panel.get_attribute('data-version-id') in (None, '')
    assert site.posts() == []
    assert not any('/api/graph/demo' in r['url'] for r in site.evidence['requests'])
    done(site)


@pytest.mark.parametrize('width', [1440, 768, 390])
def test_keyboard_xss_and_responsive_controls(live, width):
    site, page = live
    page.set_viewport_size({'width': width, 'height': 1000})
    choose_robotics(page, site)
    name = '<img src=x onerror="window.__roboticsXss=1">' + '很长的候选名称' * 20
    filename = '<img onerror=window.__roboticsXss=1>.xml'
    fill_robotics(page, name=name, filename=filename)
    for label in ['机器人本体名称', '主行为树 XML', '节点模型 XML', '机器人 profile']:
        field = page.get_by_label(label, exact=True)
        field.scroll_into_view_if_needed()
        assert field.is_visible()
        geometry = field.bounding_box()
        assert geometry and geometry['width'] >= 100 and geometry['x'] >= -1 and geometry['x']+geometry['width'] <= width+1
    button = page.get_by_role('button', name='构建机器人本体', exact=True)
    button.scroll_into_view_if_needed()
    assert button.is_enabled()
    rid = submit_from_ui(page, site, keyboard=True)
    panel = wait_run_ui(page, rid, 'completed')
    expect(panel).to_contain_text(name)
    assert panel.locator('img,script,[onerror],[onload]').count() == 0
    assert page.evaluate('window.__roboticsXss || 0') == 0
    page.get_by_role('link', name='查看本体候选', exact=True).click()
    row = site.terminal(rid)
    version = wait_version_ui(page, row['output_asset_ref'])
    expect(version).to_contain_text(name)
    assert version.locator('img,script,[onerror],[onload]').count() == 0
    for name in ['下载 OWL', '下载报告', '下载 manifest']:
        link = page.get_by_role('link', name=name, exact=True)
        link.scroll_into_view_if_needed()
        geometry = link.bounding_box()
        assert geometry and geometry['x'] >= -1 and geometry['x']+geometry['width'] <= width+1
        link.focus()
        expect(link).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth') <= width+1
    page.screenshot(path=str(site.output/f'candidate-{width}.png'), full_page=True)
    assert len(site.posts()) == 1
    done(site)


@pytest.mark.parametrize('live', [{'enabled': False}], indirect=True)
def test_disabled_robotics_preserves_data_build_and_legacy_deep_links(live):
    site, page = live
    key = site.seed_legacy()
    page.goto(site.url+'/#build')
    expect(page.locator('#bc_q')).to_be_visible()
    selector = page.get_by_label('本体构建配方', exact=True)
    expect(selector).to_have_value('data', timeout=2500)
    robot = selector.locator('option[value="robotics"]')
    assert robot.count() == 0 or robot.is_disabled()
    for fragment in ['graph='+key, 'obj='+key+':part', 'jobs', 'chat']:
        page.goto(site.url+'/#'+fragment)
        expect(page.locator('#p_graph' if fragment.startswith(('graph=', 'obj=')) else '#p_'+fragment)).to_have_class(re.compile(r'\bon\b'))
        assert page.url.endswith('#'+fragment)
    assert site.posts() == [] and not site.root.exists()
    done(site)


def test_damaged_download_is_an_error_not_a_saved_success_file(live):
    site, page = live
    row = site.submit()
    artifact = next(a for a in RunStore(site.root, read_only=True).artifacts(row['run_id']) if a['kind'] == 'ontology')
    (site.root / artifact['path']).write_bytes(b'tampered after build')
    before = RunStore(site.root, read_only=True).get_run(row['run_id'])
    ref = row['output_asset_ref']
    page.goto(site.url+f'/#library?asset={ref["ontology_id"]}&version={ref["version_id"]}')
    wait_version_ui(page, ref)
    downloads = []
    page.on('download', downloads.append)
    page.get_by_role('link', name='下载 OWL', exact=True).click()
    expect(page.get_by_test_id('robotics-version').get_by_role('alert')).to_contain_text(
        re.compile(r'artifact_integrity|损坏|完整性'), timeout=3000)
    assert not downloads
    assert RunStore(site.root, read_only=True).get_run(row['run_id']) == before
    done(site)


@pytest.mark.parametrize('live', [{'hold_owner': True}], indirect=True)
def test_persisted_untrusted_problem_text_is_rendered_as_text(live):
    """Synthetic diagnostic stored through RunStore; only a rendering/XSS check."""
    from domain_runs.build_service import BuildService
    site, page = live
    attack = '<img src=x onerror="window.__roboticsXss=1">'
    row = BuildService(site.root).submit(**upload_args('diagnostic-rendering'))
    runs = RunStore(site.root)
    runs.start(row['run_id'])
    result = failure_result('rendering_fixture')
    result['issues'][0].update(message=attack, source_role='behavior_tree',
        source_asset_id=row['build_input']['sources'][0]['source_asset_id'], location={'detail': attack})
    runs.finish_build(row['run_id'], result=result, ontology=None)
    site.evidence['diagnostic_fixture'] = 'synthetic persisted failure; not actual kernel-error evidence'
    page.goto(site.url+'/#build?recipe=robotics&run='+row['run_id'])
    panel = wait_run_ui(page, row['run_id'], 'failed')
    expect(panel).to_contain_text(attack)
    assert panel.locator('img,script,[onerror],[onload]').count() == 0
    assert page.evaluate('window.__roboticsXss || 0') == 0
    assert site.posts() == []
    done(site)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.check-results/robotics-browser')
    parser.add_argument('--case', help='Optional pytest -k filter for a named scenario')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_TEST_OUTPUT'] = str(args.output)
    command = [str(Path(__file__).resolve()), '-q', '-ra', '--junitxml='+str(args.output/'junit.xml')]
    if args.case:
        command += ['-k', args.case]
    raise SystemExit(pytest.main(command))
