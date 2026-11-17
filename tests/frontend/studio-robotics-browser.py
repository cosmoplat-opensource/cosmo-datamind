"""Real Chromium and persisted fixtures for Studio integration — DR-159 / IR-031 T04."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pytest
from playwright.sync_api import expect

# Reuse the existing owned service and scripted model boundary, without replacing product HTTP.
spec = importlib.util.spec_from_file_location('studio_design_browser_support', ROOT / 'tests/frontend/robotics-design-browser.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
browser = support.browser
live = support.live


def studio(page):
    canvas = page.locator('[data-testid="studio-panel"]')
    expect(canvas).to_be_visible()
    expect(canvas.locator('svg [data-node-id]').first).to_be_visible()
    return canvas


def test_classes_properties_instances_update_one_canvas(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site)
    sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    panel = studio(page)
    expect(panel).to_have_attribute('data-stage', 'classes')
    page.evaluate("document.querySelector('[data-testid=studio-panel] svg').dataset.retained='yes'")
    support.click_operation(page, '确认类别', '/confirm-classes')
    support.wait_design(page, sid, 'properties', 'awaiting_input')
    page.get_by_role('button', name='开始关系与属性设计', exact=True).click()
    support.wait_design(page, sid, 'properties', 'awaiting_confirmation')
    expect(panel.locator('svg')).to_have_attribute('data-retained', 'yes')
    expect(panel.locator('svg [data-edge-id]').first).to_be_visible()
    support.click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    support.wait_design(page, sid, 'abox', 'completed')
    expect(panel).to_have_attribute('data-kind', 'version', timeout=15000)
    assert len(calls) == 2
    state = site.get('/api/robotics/design-sessions/' + sid)
    ref = state['output_asset_ref']
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=studio')
    studio(page)
    assert not site.evidence['page_errors']


def test_revision_changes_preserve_view_and_history_is_read_only(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site);sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    panel = studio(page)
    panel.get_by_role('button', name='暂停布局', exact=True).click()
    node = panel.locator('svg [data-node-id]').first
    node.click()
    panel.get_by_role('button', name='固定节点', exact=True).click()
    before = node.get_attribute('transform')
    page.get_by_label('建模要求', exact=True).fill('尚未发送的文字')
    expect(node).to_have_attribute('transform', before)
    support.click_operation(page, '确认类别', '/confirm-classes')
    support.wait_design(page, sid, 'properties', 'awaiting_input')
    expect(node).to_have_attribute('transform', before)
    before_calls = len(calls)
    panel.get_by_label('设计修订', exact=True).select_option('1')
    expect(panel).to_contain_text('历史修订 1')
    expect(page.get_by_role('button', name='确认类别', exact=True)).to_have_count(0)
    panel.get_by_label('设计修订', exact=True).select_option('current')
    assert len(calls) == before_calls


def test_navigation_failure_and_read_only_actions_do_not_mutate_assets(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site);sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    studio(page)
    count = len(site.design_posts())
    page.reload();studio(page)
    assert len(site.design_posts()) == count and len(calls) == 1
    page.goto(site.url + '/#robotics-view?asset=missing&version=missing&view=studio')
    expect(page.locator('#rv_host [role=alert]')).to_be_visible()
    expect(page.locator('#rv_host svg')).to_have_count(0)
    assert len(site.design_posts()) == count


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--output', type=Path, default=ROOT / '.check-results/studio-browser')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT']=str(args.output.resolve())
    code=pytest.main([__file__,'-q','--tb=short','-o','addopts=','--junitxml='+str(args.output/'results.xml')])
    (args.output/'runner.json').write_text(json.dumps({'exit_code':code})+'\n')
    raise SystemExit(code)
