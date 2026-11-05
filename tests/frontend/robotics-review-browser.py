"""T06 RED: actual governance UI + Gunicorn + SQLite; no fulfilled core routes — DR-063 / DR-064 / DR-065."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from playwright.sync_api import expect, sync_playwright
from rdflib import Graph, RDFS

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from tests.robotics_browser_support import (LiveSite, choose_robotics, fill_robotics, submit_from_ui, wait_run_ui)
from tests.robotics_governance_support import evidence_spec, field, publish_args, review, save_args
from tests.robotics_review_browser_support import (action, bytes_snapshot, field_edit, identity, open_editor,
    ref_from_run, review_publish, save, section, version_download)
from tests.robotics_support import S, SOURCE


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
    output = Path(os.environ.get('DATAMIND_ROBOTICS_REVIEW_TEST_OUTPUT', str(ROOT / '.check-results/robotics-review-browser')))
    output = output / re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name)
    site = LiveSite(tmp_path / 'site', output)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(3500)
    page.set_default_navigation_timeout(15000)
    site.observe(page)
    try:
        yield site, page
    finally:
        try:
            page.screenshot(path=str(output / 'final.png'), full_page=True)
        except Exception as exc:
            site.evidence['screenshot_error'] = str(exc)
        finally:
            context.close()
            site.close()


def done(site):
    assert not site.evidence['page_errors'], site.evidence['page_errors']
    site.evidence['assertions_completed'] = True


def test_infrastructure_control_real_build_and_knowledge_api(live):
    site, page = live
    ref = ref_from_run(site.submit())
    page.goto(site.url + '/#library')
    data = site.get(f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}/knowledge")
    assert data['version_id'] == ref['version_id'] and data['knowledge']['entities']
    assert data['execution_readiness'] == 'not_verified'
    done(site)


def test_ui_upload_edit_compare_review_publish_download_and_restart(live):
    site, page = live
    choose_robotics(page, site)
    fill_robotics(page, name='知识维护全流程合成候选')
    rid = submit_from_ui(page, site)
    wait_run_ui(page, rid, 'completed')
    run = site.terminal(rid)
    ref = ref_from_run(run)
    assets = AssetStore(site.root, read_only=True)
    original = assets.read_payload(**ref)
    panel = open_editor(page, site, ref)
    field_edit(panel)
    saved = save(page, site, panel)
    assert saved['parent_version_id'] == ref['version_id']
    graph = Graph().parse(data=assets.read_payload(ref['ontology_id'], saved['version_id']), format='xml')
    assert str(graph.value(S.PartType_A, RDFS.label)) == '浏览器修改的零件类型'
    section(panel, '版本比较')
    panel.get_by_label('比较基线版本', exact=True).select_option(ref['version_id'])
    panel.get_by_label('比较目标版本', exact=True).select_option(saved['version_id'])
    panel.get_by_role('button', name='比较版本', exact=True).click()
    expect(panel.get_by_test_id('robotics-diff')).to_contain_text('浏览器修改的零件类型')
    page.get_by_role('link', name='机器人评审', exact=True).click()
    assert page.url.split('#')[1].startswith('review?')
    panel = page.get_by_test_id('robotics-review')
    expect(panel).to_have_attribute('data-version-id', saved['version_id'])
    published, reviewed = review_publish(page, site, panel)
    records = GovernanceService(site.root, read_only=True).artifacts(ref['ontology_id'], saved['version_id'])
    for record in records:
        path = version_download(page, site, panel, published, record['kind'], record)
        if record['kind'] != 'ontology':
            body = json.loads(path.read_text())
            assert body['version_id'] == saved['version_id'] and body['review_id'] == reviewed['review_id']
    assert assets.read_payload(**ref) == original
    assert RunStore(site.root, read_only=True).get_run(rid) == run
    deep_link = page.url
    page.reload()
    expect(page.get_by_test_id('robotics-review')).to_have_attribute('data-version-id', saved['version_id'])
    site.restart()
    page.goto(deep_link)
    panel = page.get_by_test_id('robotics-review')
    expect(panel).to_have_attribute('data-publication-state', 'published')
    expect(panel).to_contain_text('未验证')
    page.goto(site.url + '/#library?asset=' + ref['ontology_id'] + '&version=' + saved['version_id'])
    fixed = page.get_by_test_id('robotics-version')
    expect(fixed).to_have_attribute('data-version-id', saved['version_id'])
    expect(fixed).to_contain_text('结构检查：通过')
    expect(fixed).to_contain_text('任务适配：未评估')
    expect(fixed).to_contain_text('执行：未验证')
    record = next(r for r in records if r['kind'] == 'ontology')
    version_download(page, site, fixed, published, 'ontology', record)
    done(site)


def test_two_pages_keep_unsaved_draft_on_real_cas_conflict(live):
    site, page = live
    ref = ref_from_run(site.submit())
    other = page.context.new_page()
    site.observe(other)
    try:
        first = open_editor(page, site, ref)
        second = open_editor(other, site, ref)
        field_edit(first, 'first editor')
        field_edit(second, 'second editor')
        saved = save(page, site, first)
        identity(second)
        body = action(other, site, second, '保存新候选', '/drafts', status=409)
        assert body['error']['code'] == 'head_conflict'
        expect(second.get_by_role('alert')).to_contain_text('409')
        expect(second.get_by_test_id('robotics-pending-operations')).to_contain_text('second editor')
        assert second.get_attribute('data-version-id') == ref['version_id']
        versions = AssetStore(site.root, read_only=True).list_versions(ref['ontology_id'])
        assert len(versions) == 2 and versions[-1]['version_id'] == saved['version_id']
        done(site)
    finally:
        other.close()


def test_unsaved_navigation_requires_explicit_discard(live):
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    field_edit(panel, 'must retain local change')
    page.get_by_role('link', name='返回本体目录', exact=True).click()
    dialog = page.get_by_role('dialog', name='未保存的知识修改', exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(panel.get_by_test_id('robotics-pending-operations')).to_contain_text('must retain local change')
    page.get_by_role('link', name='返回本体目录', exact=True).click()
    dialog.get_by_role('button', name='放弃修改并离开', exact=True).click()
    expect(page.get_by_test_id('ontology-catalog')).to_be_visible()
    assert len(AssetStore(site.root, read_only=True).list_versions(ref['ontology_id'])) == 1
    done(site)



def test_unsaved_browser_close_uses_native_guard_and_keeps_input(live):
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    field_edit(panel, 'keep on native close')
    with page.expect_event('dialog') as pending:
        page.close(run_before_unload=True)
    dialog = pending.value
    assert dialog.type == 'beforeunload'
    dialog.dismiss()
    assert not page.is_closed()
    expect(panel.get_by_test_id('robotics-pending-operations')).to_contain_text('keep on native close')
    assert len(AssetStore(site.root, read_only=True).list_versions(ref['ontology_id'])) == 1
    done(site)


def test_business_mapping_create_and_delete_unreferenced_object(live):
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    identity(panel)
    section(panel, '知识维护')
    panel.get_by_label('编辑方式', exact=True).select_option('create_entity')
    panel.get_by_label('新对象 IRI', exact=True).fill(str(S.BrowserPart))
    panel.get_by_label('对象类型', exact=True).select_option('PartType')
    panel.get_by_label('对象名称', exact=True).fill('合成零件类型')
    panel.get_by_label('修改依据', exact=True).fill('TEST-ONLY-CREATE')
    panel.get_by_role('button', name='加入修改', exact=True).click()
    created = save(page, site, panel)
    panel.get_by_label('编辑方式', exact=True).select_option('set_relation')
    panel.get_by_label('知识对象', exact=True).select_option(str(S.BrowserPart))
    panel.get_by_label('关联关系', exact=True).select_option('hasPerceptionProfile')
    panel.get_by_label('关联对象', exact=True).select_option(str(S.Perception_Current))
    panel.get_by_label('修改依据', exact=True).fill('TEST-ONLY-MAP')
    panel.get_by_role('button', name='加入修改', exact=True).click()
    mapped = save(page, site, panel)
    assets = AssetStore(site.root, read_only=True)
    graph = Graph().parse(data=assets.read_payload(ref['ontology_id'], mapped['version_id']), format='xml')
    assert (S.BrowserPart, S.hasPerceptionProfile, S.Perception_Current) in graph
    panel.get_by_label('编辑方式', exact=True).select_option('delete_entity')
    panel.get_by_label('知识对象', exact=True).select_option(str(S.BrowserPart))
    panel.get_by_label('修改依据', exact=True).fill('TEST-ONLY-DELETE-UNREFERENCED')
    panel.get_by_role('button', name='加入修改', exact=True).click()
    deleted = save(page, site, panel)
    graph = Graph().parse(data=assets.read_payload(ref['ontology_id'], deleted['version_id']), format='xml')
    assert not list(graph.triples((S.BrowserPart, None, None)))
    assert assets.read_payload(ref['ontology_id'], created['version_id'])
    done(site)


def test_unknown_parameter_gap_and_publication_structure_gate_are_distinct(live):
    from tests.robotics_governance_support import TASK
    from tests.storage_pipeline_support import check_saved, generate, pipeline
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    field_edit(panel, '', iri=str(S.Perception_Current), field='threshold')
    save(page, site, panel)
    expect(panel).to_contain_text('未知')
    section(panel, '审核发布')
    rejected = action(page, site, panel, '接受当前版本', '/reviews', status=422)
    assert rejected['error']['code'] == 'domain_validation'
    expect(panel.get_by_role('button', name='发布当前版本', exact=True)).to_be_disabled()
    field_edit(panel, '0.72', iri=str(S.Perception_Current), field='threshold')
    save(page, site, panel)
    published, _ = review_publish(page, site, panel)
    run = generate(pipeline(), site.root, {'ontology_id': ref['ontology_id']}, published, TASK, mode='real')
    _, candidates = check_saved(site.root, run, 'needs_information')
    assert candidates == []
    expect(panel).to_contain_text('未验证')
    done(site)


def test_attachment_evidence_review_parameter_invalidation_and_revocation(live):
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    identity(panel)
    section(panel, '知识维护')
    panel.get_by_label('编辑方式', exact=True).select_option('set_relation')
    panel.get_by_label('知识对象', exact=True).select_option(str(S.PartType_A))
    panel.get_by_label('关联关系', exact=True).select_option('hasPerceptionProfile')
    panel.get_by_label('关联对象', exact=True).select_option(str(S.Perception_Current))
    panel.get_by_label('修改依据', exact=True).fill('TEST-ONLY-SCOPED-MAPPING')
    panel.get_by_role('button', name='加入修改', exact=True).click()
    save(page, site, panel)
    section(panel, '证据管理')
    panel.get_by_label('证据附件', exact=True).set_input_files({'name': 'synthetic.txt', 'mimeType': 'text/plain', 'buffer': b'TEST ONLY - no physical evidence'})
    source = action(page, site, panel, '上传证据附件', '/evidence-sources')
    assert AssetStore(site.root, read_only=True).read_source(source['source_asset_id']) == b'TEST ONLY - no physical evidence'
    spec = evidence_spec(valid_until='2999-01-01T00:00:00+00:00')
    for label, key in [('证据 IRI', 'iri'), ('证据引用', 'reference'), ('有效期', 'valid_until')]:
        panel.get_by_label(label, exact=True).fill(spec[key])
    for label, key in [('证据类型', 'kind'), ('技能实现', 'implementation'), ('适用零件类型', 'part_type')]:
        panel.get_by_label(label, exact=True).select_option(spec[key])
    registered = action(page, site, panel, '登记证据', '/evidence')
    assert source['source_asset_id'] in registered['source_asset_ids']
    evidence_row = panel.locator('[data-evidence-iri="' + spec['iri'] + '"]')
    expect(evidence_row).to_have_attribute('data-confirmation-status', 'pending')
    panel.get_by_label('待审证据', exact=True).select_option(spec['iri'])
    approved = action(page, site, panel, '接受证据', '/evidence-reviews')
    before = AssetStore(site.root, read_only=True).read_payload(ref['ontology_id'], approved['version_id'])
    expect(evidence_row).to_have_attribute('data-confirmation-status', 'confirmed')
    # Source-derived public configuration participates in evidence hashes.
    field_edit(panel, '0.81', iri=str(S.Perception_Current), field='threshold')
    saved = save(page, site, panel)
    section(panel, '证据管理')
    expect(evidence_row).to_contain_text('范围')
    inspected = GovernanceService(site.root, read_only=True).inspect(ref['ontology_id'], saved['version_id'])
    result = next(e for e in inspected['evidence'] if e['evidence_iri'] == spec['iri'])
    assert not result['valid'] and any(i['code'] == 'scope_changed' for i in result['issues'])
    panel.get_by_label('待审证据', exact=True).select_option(spec['iri'])
    action(page, site, panel, '撤销证据', '/evidence-revocations')
    expect(evidence_row).to_contain_text('撤销')
    assert GovernanceService(site.root, read_only=True).revocations(ref['ontology_id'])
    assert AssetStore(site.root, read_only=True).read_payload(ref['ontology_id'], approved['version_id']) == before
    assert saved['version_id'] != approved['version_id']
    done(site)


def test_explicit_source_rebuild_conflict_resolution_keeps_both_histories(live):
    site, page = live
    ref = ref_from_run(site.submit(name='original source'))
    panel = open_editor(page, site, ref)
    field_edit(panel, '0.71', iri=str(S.Perception_Current), field='threshold')
    current = save(page, site, panel)
    tree = ET.fromstring(SOURCE.read_bytes())
    tree.find("BehaviorTree[@ID='main']/Sequence")[4].set('threshold', '0.73')
    incoming = ref_from_run(site.submit(source=ET.tostring(tree), name='new source'))
    # Reload the same saved version to refresh catalog choices, not fake merge inputs.
    panel = open_editor(page, site, {'ontology_id': ref['ontology_id'], 'version_id': current['version_id']})
    identity(panel)
    section(panel, '重建合并')
    panel.get_by_label('原始构建版本', exact=True).select_option(ref['version_id'])
    panel.get_by_label('新来源资产', exact=True).select_option(incoming['ontology_id'])
    panel.get_by_label('新来源版本', exact=True).select_option(incoming['version_id'])
    preview = action(page, site, panel, '预览重建合并', '/merge-previews', status=200)
    assert len(preview['conflicts']) == 1
    conflict = panel.locator('[data-conflict-id="' + preview['conflicts'][0]['id'] + '"]')
    expect(panel.get_by_role('button', name='提交合并', exact=True)).to_be_disabled()
    expect(conflict).to_contain_text('0.71')
    expect(conflict).to_contain_text('0.73')
    conflict.get_by_label('冲突处理', exact=True).select_option('current')
    merged = action(page, site, panel, '提交合并', '/merges')
    assert merged['parent_version_id'] == current['version_id']
    assert merged['metadata']['merge']['incoming']['version_id'] == incoming['version_id']
    assets = AssetStore(site.root, read_only=True)
    graph = Graph().parse(data=assets.read_payload(ref['ontology_id'], merged['version_id']), format='xml')
    assert str(graph.value(S.Perception_Current, S.threshold)) == '0.71'
    assert assets.get_version(**ref) and assets.get_version(**incoming)
    done(site)


def test_rejected_review_and_changed_version_do_not_enable_publish(live):
    site, page = live
    ref = ref_from_run(site.submit())
    panel = open_editor(page, site, ref)
    identity(panel)
    section(panel, '审核发布')
    action(page, site, panel, '退回当前版本', '/reviews')
    expect(panel.get_by_role('button', name='发布当前版本', exact=True)).to_be_disabled()
    field_edit(panel, 'after rejection')
    save(page, site, panel)
    section(panel, '审核发布')
    expect(panel.get_by_role('button', name='发布当前版本', exact=True)).to_be_disabled()
    done(site)


def test_corrupt_version_download_shows_error_without_download(live):
    site, page = live
    ref = ref_from_run(site.submit())
    store = GovernanceService(site.root)
    base = store.assets.get_version(**ref)
    saved = store.save(ref['ontology_id'], **save_args(base))
    reviewed = review(store, ref['ontology_id'], saved)
    published = store.publish(ref['ontology_id'], **publish_args(saved, reviewed))
    record = next(a for a in store.artifacts(ref['ontology_id'], saved['version_id']) if a['kind'] == 'ontology')
    (site.root / record['path']).write_bytes(b'corrupted browser test artifact')
    panel = open_editor(page, site, {'ontology_id': ref['ontology_id'], 'version_id': published['version_id']})
    section(panel, '审核发布')
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    with page.expect_response(lambda r: r.url.endswith('/artifacts/' + record['artifact_id'])) as pending:
        panel.get_by_role('link', name='下载 OWL', exact=True).click()
    assert pending.value.status == 409
    expect(panel.get_by_role('alert')).to_contain_text('artifact_integrity')
    assert downloads == []
    done(site)


def test_disabled_feature_keeps_knowledge_readable_without_writes(live):
    site, page = live
    ref = ref_from_run(site.submit())
    site.stop()
    site.enabled = False
    site.env['DATAMIND_ROBOTICS_ENABLED'] = '0'
    site.start()
    before = bytes_snapshot(site.root)
    panel = open_editor(page, site, ref)
    expect(panel).to_contain_text('功能未启用')
    expect(panel.get_by_role('button', name='保存新候选', exact=True)).to_be_disabled()
    assert not [r for r in site.evidence['requests'] if r['method'] == 'POST' and '/api/ontology-assets/' in r['url']]
    assert bytes_snapshot(site.root) == before
    done(site)


@pytest.mark.parametrize('width', [1440, 768, 390])
def test_keyboard_safe_text_and_responsive_knowledge_forms(live, width):
    site, page = live
    ref = ref_from_run(site.submit())
    store = GovernanceService(site.root)
    attack = '<img src=x onerror="window.__reviewXss=1">'
    saved = store.save(ref['ontology_id'], **save_args(store.assets.get_version(**ref), operations=[field(value=attack)]))
    page.set_viewport_size({'width': width, 'height': 900})
    panel = open_editor(page, site, {'ontology_id': ref['ontology_id'], 'version_id': saved['version_id']})
    section(panel, '知识维护')
    panel.get_by_label('知识对象', exact=True).select_option(str(S.PartType_A))
    expect(panel).to_contain_text(attack)
    assert panel.locator('img,script,[onerror],[onload]').count() == 0
    assert page.evaluate('window.__reviewXss || 0') == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    for label in ['知识对象', '编辑方式', '知识字段', '字段值', '修改依据', '操作者署名', '操作理由']:
        control = panel.get_by_label(label, exact=True)
        expect(control).to_be_visible()
        box = control.bounding_box()
        assert box and box['width'] >= 80 and box['x'] >= 0 and box['x'] + box['width'] <= width + 2
    field_edit(panel, 'keyboard edit')
    identity(panel)
    button = panel.get_by_role('button', name='保存新候选', exact=True)
    button.focus()
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/drafts')) as pending:
        page.keyboard.press('Enter')
    assert pending.value.status == 201
    page.screenshot(path=str(site.output / f'knowledge-{width}.png'), full_page=True)
    done(site)


def test_legacy_data_graph_review_and_chat_have_no_robotics_editor(live):
    site, page = live
    key = site.seed_legacy()
    for route, locator in [('graph=' + key, '#g_sel'), ('review', '#rv_sel'), ('chat', '#p_chat'), ('claw', '#p_claw')]:
        page.goto(site.url + '/#' + route)
        expect(page.locator(locator)).to_be_visible()
        assert page.get_by_test_id('robotics-review').count() == 0
    done(site)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.check-results/robotics-review-browser')
    parser.add_argument('--case', help='Optional pytest -k scenario filter')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_REVIEW_TEST_OUTPUT'] = str(args.output)
    command = [str(Path(__file__).resolve()), '-q', '-ra', '--junitxml=' + str(args.output / 'junit.xml')]
    if args.case:
        command += ['-k', args.case]
    raise SystemExit(pytest.main(command))
