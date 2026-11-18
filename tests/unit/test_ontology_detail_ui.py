"""User-visible detail summaries, bounded tables and honest application state — DR-155 / T02 RED."""
from tests.browser_runtime import browser_executable

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.ontology_detail_support import NS, application_fixture, detail_fixture, mount_detail


@pytest.fixture(scope='module')
def detail_browser():
    with sync_playwright() as playwright:
        executable = browser_executable()
        browser = playwright.chromium.launch(executable_path=executable)
        yield browser
        browser.close()


@pytest.fixture
def show_detail(detail_browser):
    contexts = []

    def show(detail=None, **options):
        context, page, calls, errors = mount_detail(detail_browser, detail or detail_fixture(), **options)
        contexts.append(context)
        return page, calls, errors

    yield show
    for context in contexts:
        context.close()


def tab(page, name):
    control = page.get_by_role('tab', name=name, exact=True)
    expect(control).to_be_visible(timeout=1200)
    control.click()
    expect(control).to_have_attribute('aria-selected', 'true', timeout=1200)
    return page.get_by_role('tabpanel', name=name, exact=True)


def summary(page, label, count):
    expect(page.get_by_label(label, exact=True)).to_have_text(str(count), timeout=1200)


@pytest.mark.parametrize('kind', ['legacy', 'design', 'application'])
def test_detail_primary_actions_are_identical_for_all_robotics_versions(show_detail, kind):
    data = application_fixture() if kind == 'application' else detail_fixture(count=2)
    if kind == 'legacy':
        data.pop('design')
        data['metadata'] = {'method': 'legacy_xml'}
        data['artifacts'] = [{'kind': kind, 'download_url': '/old/' + kind} for kind in ('ontology', 'validation', 'manifest')]
    page, calls, errors = show_detail(data)
    actions = page.locator('.ra-detail-actions')
    assert actions.get_by_role('link').all_text_contents() == ['生成行为树 →', '打开图谱', '下载完整 JSON', '下载 OWL']
    for label in ('下载报告', '下载 manifest', '维护知识'):
        expect(actions.get_by_role('link', name=label, exact=True)).to_have_count(0)
    for label, suffix in [('下载完整 JSON', '/exports/json'), ('下载 OWL', '/payload')]:
        expect(actions.get_by_role('link', name=label, exact=True)).to_have_attribute(
            'href', '/api/ontology-assets/ont_detail/versions/ver_fixed' + suffix)
    assert len(calls) == 1 and not errors


def instance_rows(panel):
    return panel.get_by_role('table', name='实例列表', exact=True).locator('tbody tr')


def test_detail_open_reads_only_the_requested_fixed_version(show_detail):
    page, calls, errors = show_detail()
    expect(page.get_by_test_id('robotics-version')).to_have_attribute('data-version-id', 'ver_fixed')
    assert calls == [{'method': 'GET', 'url': 'http://ontology.test/api/ontology-assets/ont_detail/versions/ver_fixed'}]
    assert errors == []


def test_detail_header_explains_fixed_version_domain_and_build_method(show_detail):
    page, _, errors = show_detail()
    panel = page.get_by_test_id('robotics-version')
    expect(panel.get_by_role('heading', name='分页测试本体', exact=True)).to_be_visible()
    expect(panel).to_contain_text('机器人本体')
    expect(panel).to_contain_text('版本 3')
    expect(panel).to_contain_text('模型辅助', timeout=1200)
    assert errors == []


def test_summary_keeps_classes_properties_individuals_and_assertions_distinct(show_detail):
    page, _, _ = show_detail()
    summary(page, '类别数', 2)
    summary(page, '关系定义数', 1)
    summary(page, '实例数', 360)
    summary(page, '实例关系数', 359)


def test_initial_overview_does_not_mount_all_instances_or_serialize_raw_reports(show_detail):
    page, _, _ = show_detail()
    panel = page.get_by_test_id('robotics-version')
    assert panel.locator('tbody tr').count() <= 25, 'Collapsed tables still consume DOM; mount only the active page.'
    assert 'RAW_REPORT_MUST_BE_LAZY_' not in panel.text_content(), 'Raw report must wait for explicit expansion.'
    expect(page.get_by_role('tab', name='概览', exact=True)).to_have_attribute('aria-selected', 'true', timeout=1200)


def test_four_detail_tabs_have_one_active_panel_and_preserve_version(show_detail):
    page, calls, errors = show_detail()
    expect(page.get_by_role('tab')).to_have_count(4)
    expect(page.get_by_role('tab', name='应用', exact=True)).to_have_count(0)
    for name in ('概览', '类别与关系', '实例', '来源与版本'):
        panel = tab(page, name)
        expect(panel).to_be_visible()
        expect(page.get_by_role('tabpanel')).to_have_count(1)
        expect(page.get_by_test_id('robotics-version')).to_have_attribute('data-version-id', 'ver_fixed')
    assert len(calls) == 1 and errors == []


def test_instances_default_to_25_rows_with_bounded_dom(show_detail):
    page, _, _ = show_detail()
    panel = tab(page, '实例')
    expect(instance_rows(panel)).to_have_count(25)
    expect(panel).to_contain_text('调用 001')
    assert '调用 360' not in panel.text_content()
    expect(panel.get_by_role('button', name='上一页', exact=True)).to_be_disabled()
    expect(panel.get_by_role('button', name='下一页', exact=True)).to_be_enabled()
    expect(panel).to_contain_text('360')
    assert page.get_by_test_id('robotics-version').locator('tbody tr').count() <= 25


def test_instance_pagination_last_page_and_previous_do_not_duplicate_rows(show_detail):
    page, calls, _ = show_detail()
    panel = tab(page, '实例')
    for _ in range(14):
        panel.get_by_role('button', name='下一页', exact=True).click()
    expect(instance_rows(panel)).to_have_count(10)
    expect(panel).to_contain_text('调用 351')
    expect(panel).to_contain_text('调用 360')
    expect(panel.get_by_role('button', name='下一页', exact=True)).to_be_disabled()
    panel.get_by_role('button', name='上一页', exact=True).click()
    expect(instance_rows(panel)).to_have_count(25)
    expect(panel).to_contain_text('调用 326')
    assert '调用 351' not in panel.text_content()
    assert len(calls) == 1


@pytest.mark.parametrize('query', ['调用 360', 'call_360', NS + 'Call_360'])
def test_search_matches_instance_name_or_full_id_without_case_sensitivity(show_detail, query):
    page, _, _ = show_detail()
    panel = tab(page, '实例')
    panel.get_by_role('textbox', name='搜索实例', exact=True).fill(query)
    expect(instance_rows(panel)).to_have_count(1)
    expect(panel).to_contain_text('调用 360')
    summary(page, '实例数', 360)


def test_search_and_type_filter_intersect_and_reset_to_first_page(show_detail):
    data = detail_fixture()
    row = data['detail_view']['individuals'][-1]
    row['class_ids'] = [NS + 'Workpiece']
    row['group'] = 'business'
    data['design']['abox']['individuals'][-1] = dict(row)
    page, _, _ = show_detail(data)
    panel = tab(page, '实例')
    panel.get_by_role('button', name='下一页', exact=True).click()
    panel.get_by_role('combobox', name='实例类型', exact=True).select_option(NS + 'Workpiece')
    expect(instance_rows(panel)).to_have_count(1)
    expect(panel).to_contain_text('调用 360')
    expect(panel.get_by_role('button', name='上一页', exact=True)).to_be_disabled()
    panel.get_by_role('textbox', name='搜索实例', exact=True).fill('调用 001')
    expect(panel).to_contain_text('没有匹配的实例')
    panel.get_by_role('combobox', name='实例类型', exact=True).select_option('')
    expect(instance_rows(panel)).to_have_count(1)
    expect(panel).to_contain_text('调用 001')
    panel.get_by_role('textbox', name='搜索实例', exact=True).fill('')
    expect(instance_rows(panel)).to_have_count(25)
    expect(panel).to_contain_text('调用 001')


def test_empty_business_group_does_not_claim_the_ontology_is_empty(show_detail):
    page, _, _ = show_detail()
    panel = tab(page, '实例')
    panel.get_by_role('combobox', name='展示范围', exact=True).select_option('business')
    expect(panel).to_contain_text('尚未包含业务对象与配置实例')
    expect(panel).to_contain_text('类别定义不等于实例')
    assert '本体为空' not in panel.inner_text()
    summary(page, '实例数', 360)
    panel.get_by_role('combobox', name='展示范围', exact=True).select_option('skills')
    expect(instance_rows(panel)).to_have_count(25)


def test_declared_classes_without_individuals_remain_a_valid_empty_instance_view(show_detail):
    page, _, errors = show_detail(detail_fixture(count=0))
    summary(page, '类别数', 2)
    summary(page, '实例数', 0)
    summary(page, '实例关系数', 0)
    panel = tab(page, '实例')
    expect(panel).to_contain_text('尚无实例')
    assert errors == []


def test_schema_tables_show_definitions_without_turning_classes_into_instances(show_detail):
    page, _, _ = show_detail()
    panel = tab(page, '类别与关系')
    expect(panel.get_by_role('table', name='类别列表', exact=True).locator('tbody tr')).to_have_count(2)
    expect(panel.get_by_role('table', name='关系列表', exact=True).locator('tbody tr')).to_have_count(1)
    expect(panel.get_by_role('table', name='数据属性列表', exact=True).locator('tbody tr')).to_have_count(1)
    expect(panel).to_contain_text('工件')
    assert '调用 360' not in panel.text_content()
    summary(page, '实例数', 360)


@pytest.mark.parametrize(('status', 'freshness', 'message'), [
    ('not_assessed', None, '尚未检查'),
    ('supported', 'current', '可准备任务'),
    ('unsupported', 'current', '有缺项'),
    ('supported', 'stale', '检查已过期'),
    ('unsupported', 'stale', '检查已过期'),
    ('supported', 'unavailable', '检查已过期'),
])
def test_application_state_does_not_promote_unchecked_or_stale_reports(show_detail, status, freshness, message):
    page, calls, _ = show_detail(detail_fixture(status=status, freshness=freshness))
    panel = tab(page, '概览')
    expect(panel).to_contain_text(message)
    assert '本任务可生成' not in panel.inner_text()
    if freshness not in (None, 'current'):
        assert '可准备任务' not in panel.inner_text(), 'A stale supported report is historical, not current availability.'
    assert all(call['method'] == 'GET' for call in calls)


def test_design_application_task_and_execution_are_independent_states(show_detail):
    page, _, _ = show_detail(detail_fixture(status='supported', freshness='current'))
    panel = tab(page, '概览')
    expect(panel).to_contain_text('设计')
    expect(panel).to_contain_text('可准备任务')
    expect(panel).to_contain_text('尚未提供任务')
    expect(panel).to_contain_text('执行未验证')
    assert '执行已验证' not in panel.inner_text()


def test_application_version_displays_current_facts_instead_of_parent_snapshot(show_detail):
    page, _, errors = show_detail(application_fixture())
    summary(page, '实例数', 2)
    panel = tab(page, '实例')
    expect(instance_rows(panel)).to_have_count(2)
    expect(panel).to_contain_text('调用 002')
    assert errors == []


def test_untrusted_instance_names_are_rendered_as_text(show_detail):
    data = detail_fixture(count=1)
    attack = '<img src=x onerror="window.__detail_xss=1">'
    data['detail_view']['individuals'][0]['label'] = attack
    data['design']['abox']['individuals'][0]['label'] = attack
    page, _, errors = show_detail(data)
    panel = tab(page, '实例')
    expect(panel).to_contain_text(attack)
    expect(panel.locator('img')).to_have_count(0)
    assert page.evaluate('window.__detail_xss || 0') == 0 and errors == []


def test_partial_source_coverage_is_visible_without_mounting_the_raw_report(show_detail):
    page, _, _ = show_detail()
    panel = tab(page, '概览')
    expect(panel).to_contain_text('部分覆盖')
    assert 'RAW_REPORT_MUST_BE_LAZY_' not in panel.text_content()


@pytest.mark.parametrize('width', [1440, 390])
def test_detail_tabs_and_long_identifiers_do_not_expand_page_width(show_detail, width):
    data = detail_fixture(count=1)
    data['detail_view']['individuals'][0]['id'] = NS + 'long_identifier_' * 40
    data['design']['abox']['individuals'][0]['id'] = data['detail_view']['individuals'][0]['id']
    page, _, errors = show_detail(data, width=width)
    for name in ('概览', '类别与关系', '实例', '来源与版本'):
        tab(page, name)
        dimensions = page.get_by_test_id('robotics-version').evaluate('''node => ({
          own: node.scrollWidth - node.clientWidth,
          page: document.documentElement.scrollWidth - window.innerWidth,
          main: document.querySelector('main').scrollWidth - document.querySelector('main').clientWidth
        })''')
        assert max(dimensions.values()) <= 1, (name, width, dimensions)
    assert errors == []
