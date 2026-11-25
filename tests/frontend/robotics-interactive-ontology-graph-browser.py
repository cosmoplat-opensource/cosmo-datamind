"""Real Chromium and persisted fixtures for Ontology graph integration — DR-166 / IR-031 T04."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pytest
from playwright.sync_api import expect

pytest_plugins = ['tests.robotics_graph_browser_evidence']

# Reuse the existing owned service and scripted model boundary, without replacing product HTTP.
spec = importlib.util.spec_from_file_location('interactive_ontology_graph_design_browser_support', ROOT / 'tests/frontend/robotics-design-browser.py')
assert spec is not None and spec.loader is not None
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
browser = support.browser
live = support.live
prepared_seeds = support.prepared_seeds


@pytest.fixture(autouse=True)
def no_page_errors(live):
    yield
    assert live[0].evidence['page_errors'] == []


def interactive_graph_panel(page):
    canvas = page.locator('[data-testid="interactive-ontology-graph-panel"]')
    expect(canvas).to_be_visible()
    expect(canvas.locator('svg [data-node-id]').first).to_be_visible()
    return canvas


@pytest.mark.parametrize('live', [{'prepare': support.prepare_candidate}], indirect=True)
def test_recycle_bin_graph_round_trip_preserves_fixed_version(live):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply

    site, page, calls = live
    ref = site.prepared['output_asset_ref']
    oid, vid = ref['ontology_id'], ref['version_id']
    apply(LifecycleService(site.root), oid)
    before = support.db_rows(site.root), support.blob_bytes(site.root)
    page.goto(site.url + '/#library?state=deleted')
    card = page.locator(f'[data-ontology-id="{oid}"]')
    card.get_by_role('link', name='查看固定版本').click()
    graph = page.get_by_role('link', name='打开图谱', exact=True)
    expect(graph).to_have_attribute('href', re.compile(r'&state=deleted(?:&|$)'))
    graph.click()
    expect(interactive_graph_panel(page)).to_have_attribute('data-kind', 'version')
    expect(page.get_by_role('button', name='编辑副本', exact=True)).to_have_count(0)
    expect(page.locator('#rv_host header')).to_contain_text('历史查看')
    expect(page.locator('#rv_host').get_by_role('link', name='基于此本体生成行为树')).to_have_count(0)
    page.locator('#rv_host').get_by_role('link', name='返回本体详情').click()
    expect(page).to_have_url(site.url + f'/#library?asset={oid}&version={vid}&state=deleted')
    expect(page.get_by_test_id('robotics-version')).to_have_attribute('data-version-id', vid)
    assert (support.db_rows(site.root), support.blob_bytes(site.root)) == before
    assert calls == []



@pytest.mark.parametrize('live', [{'prepare': support.prepare_candidate}], indirect=True)
def test_edit_copy_create_cancel_delete_undo_redo_export(live):
    from rdflib import Graph, OWL, RDF, URIRef
    site, page, _ = live
    ref = site.prepared['output_asset_ref']
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id']
              + '&view=interactive-ontology-graph')
    panel = interactive_graph_panel(page)
    before = support.db_rows(site.root), support.blob_bytes(site.root)
    panel.get_by_role('button', name='编辑副本', exact=True).click()
    panel.get_by_role('button', name='新增实例', exact=True).first.click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('名称', exact=True).fill('样例实例')
    iri = dialog.get_by_label('IRI', exact=True).input_value()
    assert iri
    dialog.get_by_role('button', name='应用').click()
    details = panel.locator('.interactive-ontology-graph-entity')
    expect(details.get_by_role('heading', name='样例实例')).to_be_visible()
    expect(panel.get_by_label('节点详情').locator('optgroup[label="未分类实例"] option').filter(has_text='样例实例')).to_have_count(1)
    expect(page.locator('#rv_host header')).to_contain_text('编辑副本')
    expect(page.locator('#rv_host header').get_by_role('link', name='基于此本体生成行为树')).to_have_count(0)
    details.get_by_role('button', name='删除实体').click()
    expect(dialog).to_contain_text('2 条')
    dialog.get_by_role('button', name='取消').click()
    expect(details.get_by_role('heading', name='样例实例')).to_be_visible()
    details.get_by_role('button', name='删除实体').click()
    dialog.get_by_role('button', name='确认删除').click()
    selector = panel.get_by_label('节点详情')
    expect(selector.locator(f'option[value="{iri}"]')).to_have_count(0)
    panel.get_by_role('button', name='撤销', exact=True).click()
    expect(selector.locator(f'option[value="{iri}"]')).to_have_count(1)
    panel.get_by_role('button', name='重做', exact=True).click()
    expect(selector.locator(f'option[value="{iri}"]')).to_have_count(0)
    panel.get_by_role('button', name='撤销', exact=True).click()
    with page.expect_download() as download:
        panel.get_by_role('button', name='导出副本').click()
    exported = Graph().parse(download.value.path(), format='xml')
    assert (URIRef(iri), RDF.type, OWL.NamedIndividual) in exported
    assert (support.db_rows(site.root), support.blob_bytes(site.root)) == before



@pytest.mark.parametrize('live', [{'prepare': support.prepare_candidate}], indirect=True)
def test_edit_copy_unlinks_one_instance_relation_and_cancels_pending_edit(live):
    site, page, _ = live
    ref = site.prepared['output_asset_ref']
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id']
              + '&view=interactive-ontology-graph')
    panel = interactive_graph_panel(page)
    panel.get_by_role('button', name='编辑副本', exact=True).click()
    dialog = page.get_by_role('dialog')
    def create(title, name):
        panel.get_by_role('button', name='新增' + title, exact=True).first.click()
        dialog.get_by_label('名称', exact=True).fill(name)
        iri = dialog.get_by_label('IRI', exact=True).input_value()
        dialog.get_by_role('button', name='应用', exact=True).click()
        expect(dialog).to_have_count(0)
        return iri
    prop = create('对象属性', '样例关联')
    a = create('实例', '左端')
    b = create('实例', '右端')
    panel.get_by_role('button', name='新增关系').click()
    dialog.get_by_label('关系类型').select_option(label='实例关系')
    for title, iri in [('起点', a), ('对象属性', prop), ('终点', b)]:
        dialog.get_by_label(title, exact=True).select_option(iri)
    dialog.get_by_role('button', name='应用').click()
    details = panel.locator('.interactive-ontology-graph-entity')
    edge = details.get_by_role('listitem').filter(has_text='样例关联')
    expect(edge).to_contain_text('左端 → 样例关联 → 右端')
    edge.get_by_role('button', name='解除关系').click()
    expect(dialog).to_contain_text('移除 1 条')
    dialog.get_by_role('button', name='确认删除').click()
    expect(edge).to_have_count(0)
    selector = panel.get_by_label('节点详情')
    for iri in (a, b, prop):
        expect(selector.locator(f'option[value="{iri}"]')).to_have_count(1)
    details.get_by_role('button', name='编辑实体').click()
    dialog.get_by_label('中文名称').fill('应取消')
    with page.expect_request('**/graph-edit-copy'):
        page.route('**/graph-edit-copy', lambda route: None)
        dialog.get_by_role('button', name='应用').click()
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(details.get_by_role('heading', name='左端')).to_be_visible()
    page.unroute('**/graph-edit-copy')
    panel.get_by_role('button', name='撤销', exact=True).click()
    expect(edge).to_be_visible()


def test_classes_properties_instances_update_one_canvas(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site)
    sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    panel = interactive_graph_panel(page)
    expect(panel).to_have_attribute('data-stage', 'classes')
    page.evaluate("document.querySelector('[data-testid=interactive-ontology-graph-panel] svg').dataset.retained='yes'")
    support.click_operation(page, '确认类别', '/confirm-classes')
    support.wait_design(page, sid, 'properties', 'awaiting_input')
    page.get_by_role('button', name='开始关系与属性设计', exact=True).click()
    support.wait_design(page, sid, 'properties', 'awaiting_confirmation')
    expect(panel.locator('svg')).to_have_attribute('data-retained', 'yes')
    expect(panel.locator('svg [data-edge-id]').first).to_be_visible()
    support.click_operation(page, '确认骨架并构建实例', '/confirm-schema')
    expect(page).to_have_url(re.compile(r'#robotics-view\?.*view=interactive-ontology-graph'), timeout=25000)
    expect(interactive_graph_panel(page)).to_have_attribute('data-kind', 'version', timeout=15000)
    assert len(calls) == 2
    state = site.get('/api/robotics/design-sessions/' + sid)
    ref = state['output_asset_ref']
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=interactive-ontology-graph')
    interactive_graph_panel(page)
    assert not site.evidence['page_errors']


def test_revision_changes_preserve_view_and_history_is_read_only(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site);sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    panel = interactive_graph_panel(page)
    panel.get_by_role('button', name='暂停布局', exact=True).click()
    node = panel.locator('svg [data-node-id]').first
    node.click()
    panel.get_by_role('button', name='固定节点', exact=True).click()
    before = node.get_attribute('transform')
    page.get_by_text('继续提修改意见', exact=True).click()
    page.get_by_label('建模要求', exact=True).fill('尚未发送的文字')
    expect(node).to_have_attribute('transform', before)
    support.click_operation(page, '确认类别', '/confirm-classes')
    support.wait_design(page, sid, 'properties', 'awaiting_input')
    expect(node).to_have_attribute('transform', before)
    before_calls = len(calls)
    page.get_by_label('设计修订', exact=True).select_option('1')
    expect(page.locator('.rd-status')).to_contain_text('历史修订 1 · 只读')
    expect(page.get_by_role('button', name='确认类别', exact=True)).to_have_count(0)
    page.get_by_label('设计修订', exact=True).select_option('current')
    assert len(calls) == before_calls


def test_navigation_failure_and_read_only_actions_do_not_mutate_assets(live):
    site, page, calls = live
    admitted = support.ui_admit(page, site);sid = admitted['session_id']
    support.wait_design(page, sid, 'classes', 'awaiting_confirmation')
    interactive_graph_panel(page)
    count = len(site.design_posts())
    page.reload();interactive_graph_panel(page)
    assert len(site.design_posts()) == count and len(calls) == 1
    page.goto(site.url + '/#robotics-view?asset=missing&version=missing&view=interactive-ontology-graph')
    expect(page.locator('#rv_host [role=alert]')).to_be_visible()
    expect(page.locator('#rv_host svg')).to_have_count(0)
    assert len(site.design_posts()) == count


def probe(page, site, *, nodes=2, edges=1, instances=0):
    page.goto(site.url + '/#library')
    page.evaluate('''async ({nodes,edges,instances})=>{
      const {mountPanel}=await import('/assets/modules/interactive-ontology-graph/panel.js');
      const host=document.createElement('div');host.id='interactive_ontology_graph_probe';document.body.replaceChildren(host);
      const ns='https://probe.test/#';
      const doc={schema:'datamind-interactive-ontology-graph/1',context:{kind:'version',key:'probe',revision:'fixed',stage:'fixed',status:'candidate'},metadata:{read_only:true},warnings:[],properties:[],relations:[],
        nodes:Array.from({length:nodes},(_,i)=>({id:ns+i,iri:ns+i,type:'class',labels:[{value:'类别 '+i,language:'zh'}],definition:'用于验证图谱呈现',individuals:i===0?Array.from({length:instances},(_,j)=>ns+'i'+j):[]})),
        individuals:Array.from({length:instances},(_,j)=>({id:ns+'i'+j,iri:ns+'i'+j,types:[ns+'0'],labels:[{value:'实例 '+j}],data:[]})),
        edges:Array.from({length:edges},(_,i)=>({id:'edge'+i,source:ns+(i%nodes),target:ns+((i+1)%nodes),predicate:ns+'关联',kind:'object',labels:[{value:'关联'}]}))};
      const panel=mountPanel(host);panel.present(doc);window.interactiveGraphProbe={panel,doc,mountPanel,host};
    }''', {'nodes': nodes, 'edges': edges, 'instances': instances})
    return page.locator('#interactive_ontology_graph_probe')


def test_class_focus_immediately_offers_restore_without_selecting_another_node(live):
    """DR-171: the current selection must re-render after a focus change."""
    site, page, _ = live
    panel = probe(page, site, nodes=3, edges=1)
    panel.get_by_label('节点详情', exact=True).select_option('https://probe.test/#0')
    panel.get_by_role('button', name='仅显示此类关联', exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(2)
    restore = panel.get_by_role('button', name='显示完整图谱', exact=True)
    expect(restore).to_be_visible()
    restore.click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(3)
    expect(restore).to_have_count(0)
    expect(panel.locator('.interactive-ontology-graph-entity h3')).to_have_text('类别 0')
    assert not site.evidence['page_errors']


@pytest.mark.parametrize('live', [{'prepare': support.prepare_candidate}], indirect=True)
def test_graph_back_restores_enabled_controls_without_writes(live):
    site, page, calls = live; ref = site.prepared['output_asset_ref']
    before = support.db_rows(site.root)
    page.goto(site.url + '/#robotics-view?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'] + '&view=interactive-ontology-graph')
    panel = interactive_graph_panel(page)
    panel.get_by_role('button', name='暂停布局', exact=True).click()
    page.goto(site.url + '/#library')
    expect(page.get_by_test_id('ontology-catalog')).to_be_visible()
    page.go_back(); interactive_graph_panel(page)
    for name in ('暂停布局', '适应画布', '可读视图'):
        expect(panel.get_by_role('button', name=name, exact=True)).to_be_enabled()
    assert support.db_rows(site.root) == before and calls == []


@pytest.mark.parametrize('nodes, edges', [(301, 0), (3, 601)])
def test_first_oversized_graph_can_open_a_class_from_the_index(live, nodes, edges):
    site, page, _ = live
    if edges:
        cdp = page.context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': 2})
    panel = probe(page, site, nodes=nodes, edges=edges)
    options = panel.get_by_label('节点详情', exact=True)
    notice = panel.locator('[role=status]')
    visible = panel.locator('svg [data-node-id]')
    counts = panel.locator('.interactive-ontology-graph-counts')
    expect(visible).to_have_count(0)
    expect(notice).to_contain_text('类别')
    expect(options.locator('option')).to_have_count(nodes + 1)
    expect(counts).to_contain_text(f'{nodes} 个类别')
    expect(panel).to_have_attribute('data-revision', 'fixed')

    if edges:
        panel.get_by_label('查找节点', exact=True).fill('类别 0')
        panel.get_by_role('button', name='查找', exact=True).click()
    else:
        options.select_option('https://probe.test/#0')
    local_nodes = 3 if edges else 1
    expect(visible).to_have_count(local_nodes)
    expect(counts).to_contain_text(f'当前显示 {local_nodes} 个节点')
    expect(panel.locator('.interactive-ontology-graph-entity h3')).to_have_text('类别 0')
    restore = panel.get_by_role('button', name='显示完整图谱', exact=True)
    if edges:
        assert page.evaluate('interactiveGraphProbe.panel.controller.graph.frame > 0')
    restore.click()
    expect(notice).to_contain_text('范围')
    expect(visible).to_have_count(local_nodes)
    expect(restore).to_be_visible()

    target = nodes - 1
    panel.get_by_label('查找节点', exact=True).fill(f'类别 {target}')
    panel.get_by_role('button', name='查找', exact=True).click()
    expect(panel.locator('.interactive-ontology-graph-entity h3')).to_have_text(f'类别 {target}')
    expect(options).to_have_value(f'https://probe.test/#{target}')
    expect(panel.locator(f'svg [data-node-id="https://probe.test/#{target}"]')).to_have_count(1)
    expect(visible).to_have_count(local_nodes)
    expect(notice).to_be_empty()
    expect(panel.locator('svg [data-edge-id]')).to_have_count(400 if edges else 0)


def test_oversized_class_neighborhood_preserves_last_displayed_scope(live):
    site, page, _ = live
    panel = probe(page, site, nodes=302, edges=0)
    page.evaluate("""()=>{const {panel,doc}=interactiveGraphProbe;
      const [hub,...others]=doc.nodes;
      const edges=others.slice(0,300).map(node=>({id:node.id,source:hub.id,target:node.id,
        predicate:'urn:test:related',kind:'object'}));
      panel.present({...doc,context:{...doc.context,key:'with-hub'},edges});
    }""")
    options = panel.get_by_label('节点详情', exact=True)
    visible = panel.locator('svg [data-node-id]')
    options.select_option('https://probe.test/#0')
    expect(panel.get_by_role('status')).to_contain_text('范围')
    expect(visible).to_have_count(0)
    options.select_option('https://probe.test/#301')
    expect(visible).to_have_count(1)
    options.select_option('https://probe.test/#0')
    expect(panel.get_by_role('status')).to_contain_text('范围')
    expect(options).to_have_value('https://probe.test/#301')
    expect(panel.locator('.interactive-ontology-graph-entity h3')).to_have_text('类别 301')
    expect(visible).to_have_count(1)
    options.select_option('https://probe.test/#1')
    expect(visible).to_have_count(2)
    expect(panel.locator('svg [data-edge-id]')).to_have_count(1)


def test_panel_rejects_oversized_revision_without_changing_caption(live):
    site, page, _ = live
    panel = probe(page, site)
    page.evaluate("""()=>{const {panel,doc}=interactiveGraphProbe;doc.context.kind='design';doc.context.revision=1;panel.present(doc);panel.controller.pause(true);
      const next=structuredClone(doc);next.context.revision=2;
      next.nodes=Array.from({length:301},(_,i)=>({...doc.nodes[0],id:'https://probe.test/#new'+i}));
      panel.present(next,{scope:JSON.stringify([doc.context.kind,doc.context.key])});
    }""")
    expect(panel).to_have_attribute('data-revision', '1')
    expect(panel.get_by_label('节点详情', exact=True).locator('option')).to_have_count(3)
    expect(panel.get_by_label('节点详情', exact=True).locator('option').last).to_have_attribute('value', 'https://probe.test/#1')
    expect(panel.locator('svg [data-node-id]')).to_have_count(2)
    expect(panel.locator('.interactive-ontology-graph-notice')).to_contain_text('范围')


def test_property_search_shows_definition_and_constraint_details(live):
    site, page, _ = live
    panel = probe(page, site)
    page.evaluate("""()=>{const {panel,doc}=interactiveGraphProbe;doc.properties=[{id:'https://probe.test/#p',
      type:'object',labels:[{value:'专用关系'}],definition:'关系定义说明',domains:[doc.nodes[0].id],ranges:[doc.nodes[1].id]}];
      panel.present(doc);panel.controller.state.focusClass(doc.nodes[0].id);panel.controller.draw();
    }""")
    panel.get_by_label('查找节点', exact=True).fill('专用关系')
    panel.get_by_role('button', name='查找', exact=True).click()
    expect(panel.locator('.interactive-ontology-graph-entity h3')).to_have_text('专用关系')
    expect(panel.locator('.interactive-ontology-graph-entity')).to_contain_text('关系定义说明')
    expect(panel.locator('.interactive-ontology-graph-entity')).to_contain_text('类别 0')
    expect(panel.locator('.interactive-ontology-graph-entity')).to_contain_text('类别 1')
    assert page.evaluate('interactiveGraphProbe.panel.controller.state.selectedId') == 'https://probe.test/#p'
    assert page.evaluate('interactiveGraphProbe.panel.controller.state.classFocusId') is None


def test_force_drag_pin_search_zoom_and_keyboard_details(live):
    site, page, calls = live
    panel = probe(page, site, nodes=2)
    page.evaluate('''()=>{const c=interactiveGraphProbe.panel.controller;c.pause(true);
      Object.assign(c.graph.positions.get('https://probe.test/#0'),{x:-100,y:0,vx:0,vy:0});
      Object.assign(c.graph.positions.get('https://probe.test/#1'),{x:100,y:0,vx:0,vy:0});
      c.graph.restoreView({...c.graph.getView(),transform:{x:0,y:0,k:1},paused:true});c.pause(false);
    }''')
    # Capture the real pointer-to-next-frame delay; rendering remains unmodified.
    page.evaluate('''()=>{window.pointerDelay=null;document.querySelector('#interactive_ontology_graph_probe svg').addEventListener('pointermove',()=>{
      const start=performance.now();requestAnimationFrame(()=>{window.pointerDelay=performance.now()-start;});});}''')
    node = panel.locator('[data-node-id="https://probe.test/#0"]')
    box = node.bounding_box(); assert box
    before = page.evaluate("interactiveGraphProbe.panel.controller.graph.positions.get('https://probe.test/#1').x")
    page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
    page.mouse.down();page.mouse.move(box['x']+box['width']/2-100,box['y']+box['height']/2,steps=5);page.mouse.up()
    page.wait_for_timeout(800)
    after = page.evaluate("interactiveGraphProbe.panel.controller.graph.positions.get('https://probe.test/#1').x")
    assert abs(after-before)>=2
    assert page.evaluate('window.pointerDelay')<=100
    node.focus();page.keyboard.press('Enter')
    expect(panel.locator('.interactive-ontology-graph-details h3')).to_have_text('类别 0')
    fixed=page.evaluate("interactiveGraphProbe.panel.controller.graph.positions.get('https://probe.test/#0').x")
    page.wait_for_timeout(200)
    assert abs(page.evaluate("interactiveGraphProbe.panel.controller.graph.positions.get('https://probe.test/#0').x")-fixed)<=.5
    panel.get_by_role('button',name='释放节点',exact=True).click()
    panel.get_by_label('查找节点',exact=True).fill('类别 1');panel.get_by_role('button',name='查找',exact=True).click()
    expect(panel.locator('.interactive-ontology-graph-details h3')).to_have_text('类别 1')
    zoom=page.evaluate('interactiveGraphProbe.panel.controller.graph.transform.k')
    panel.get_by_role('button',name='放大',exact=True).click()
    assert page.evaluate('interactiveGraphProbe.panel.controller.graph.transform.k')>zoom
    assert calls==[]


def test_class_expansion_empty_shared_and_paged_instances(live):
    site, page, calls = live
    panel=probe(page,site,nodes=2,instances=1000)
    panel.get_by_role('button',name='暂停布局',exact=True).click()
    node=panel.locator('[data-node-id="https://probe.test/#0"]')
    node.dblclick()
    # The browser actually dispatches double click; then details report the same count.
    expect(panel.locator('svg [data-node-id]')).to_have_count(52)
    node.click()
    expect(panel.locator('.interactive-ontology-graph-details')).to_contain_text('已显示 50 / 1000')
    panel.get_by_role('button',name='继续显示实例',exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(102)
    expect(panel.locator('.interactive-ontology-graph-details')).to_contain_text('已显示 100 / 1000')
    panel.get_by_role('button',name='下一页实例',exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(52)
    expect(panel.locator('.interactive-ontology-graph-details')).to_contain_text('范围 101～150')
    for _ in range(17):
        panel.get_by_role('button',name='下一页实例',exact=True).click()
    expect(panel.locator('.interactive-ontology-graph-details')).to_contain_text('范围 951～1000')
    expect(panel.get_by_role('button',name='下一页实例',exact=True)).to_have_count(0)
    panel.get_by_role('button',name='收起实例',exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(2)
    panel.locator('[data-node-id="https://probe.test/#1"]').dblclick()
    expect(panel.locator('[role=status]')).to_contain_text('没有显式归属实例')
    page.set_viewport_size({'width':390,'height':844})
    assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1')
    assert calls==[]


def test_representative_scale_performance_and_disposal(live):
    site,page,calls=live
    probe(page,site,nodes=30,edges=60,instances=150)
    measured=page.evaluate('''async ()=>{
      const {panel,doc,mountPanel,host}=interactiveGraphProbe;
      const samples=[];
      panel.controller.pause(true);
      for(let i=0;i<6;i++){
        const next=structuredClone(doc);next.context.kind='design';next.context.revision=i;
        const scope='measure';const started=performance.now();panel.present(next,{scope});
        await new Promise(requestAnimationFrame);if(i)samples.push(performance.now()-started);
      }
      const dense=structuredClone(doc);dense.context.key='dense';dense.context.revision=0;
      dense.nodes=Array.from({length:150},(_,i)=>({...doc.nodes[0],id:'d'+i,iri:'d'+i,individuals:[]}));
      dense.individuals=[];dense.edges=Array.from({length:300},(_,i)=>({id:'e'+i,source:'d'+i%150,target:'d'+(i+1)%150,predicate:'link',kind:'object'}));
      const large=[];
      for(let i=0;i<6;i++){dense.context.revision=i;const start=performance.now();panel.present(dense,{scope:'dense'});panel.controller.pause(true);await new Promise(requestAnimationFrame);if(i)large.push(performance.now()-start);}
      panel.destroy();
      const realRaf=window.requestAnimationFrame,realCancel=window.cancelAnimationFrame,RealObserver=window.ResizeObserver;
      const pending=new Set(),observers=new Set();
      window.requestAnimationFrame=fn=>{let id=realRaf(t=>{pending.delete(id);fn(t);});pending.add(id);return id;};
      window.cancelAnimationFrame=id=>{pending.delete(id);realCancel(id);};
      window.ResizeObserver=class extends RealObserver{observe(e){observers.add(this);super.observe(e);}disconnect(){observers.delete(this);super.disconnect();}};
      for(let i=0;i<20;i++){const p=mountPanel(host);p.present(doc);p.destroy();}
      const resources={frames:pending.size,observers:observers.size,children:host.children.length};
      window.requestAnimationFrame=realRaf;window.cancelAnimationFrame=realCancel;window.ResizeObserver=RealObserver;
      return {normal:samples,dense:large,resources,userAgent:navigator.userAgent};
    }''')
    assert max(measured['normal'])<=2000,measured
    assert max(measured['dense'])<=5000,measured
    assert measured['resources']=={'frames':0,'observers':0,'children':0}
    site.evidence['interactive_ontology_graph_performance']=measured
    assert calls==[]


def test_late_fixed_result_during_history_switch_is_cached_without_overwriting_history(live):
    site,page,calls=live
    admitted=support.ui_admit(page,site);sid=admitted['session_id']
    support.wait_design(page,sid,'classes','awaiting_confirmation');panel=interactive_graph_panel(page)
    support.click_operation(page,'确认类别','/confirm-classes')
    support.wait_design(page,sid,'properties','awaiting_input')
    page.get_by_role('button',name='开始关系与属性设计',exact=True).click()
    support.wait_design(page,sid,'properties','awaiting_confirmation')
    support.click_operation(page,'确认骨架并构建实例','/confirm-schema')
    expect(page).to_have_url(re.compile(r'#robotics-view\?'),timeout=25000)
    expect(interactive_graph_panel(page)).to_have_attribute('data-kind','version')
    # Delay delivery of a genuine server response; never replace its bytes or status.
    page.evaluate('''()=>{const original=window.fetch;window.fetch=async(...args)=>{
      const response=await original(...args);
      if(String(args[0]).endsWith('/views/interactive-ontology-graph'))await new Promise(resolve=>{window.releaseInteractiveGraphResult=resolve;});
      return response;
    };}''')
    page.goto(site.url+'/#build?recipe=robotics&session='+sid)
    support.wait_design(page,sid,'abox','completed')
    page.wait_for_function('typeof window.releaseInteractiveGraphResult === "function"')
    page.get_by_label('设计修订',exact=True).select_option('1')
    expect(page.locator('.rd-status')).to_contain_text('历史修订 1 · 只读')
    page.evaluate('window.releaseInteractiveGraphResult()')
    page.wait_for_function('!!document.querySelector("[data-testid=interactive-ontology-graph-panel]").interactiveGraphResult')
    expect(page.locator('.rd-status')).to_contain_text('历史修订 1 · 只读')
    page.get_by_label('设计修订',exact=True).select_option('current')
    expect(panel).to_have_attribute('data-kind','version')
    assert len(calls)==2


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--output', type=Path, default=ROOT / '.check-results/interactive-ontology-graph-browser')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT']=str(args.output.resolve())
    code=pytest.main([__file__,'-q','--tb=short','-o','addopts=','--junitxml='+str(args.output/'results.xml')])
    from tests.robotics_graph_browser_evidence import check_junit_evidence
    errors = check_junit_evidence(args.output)
    for error in errors:
        print(error, file=sys.stderr)
    if errors and not code:
        code = pytest.ExitCode.TESTS_FAILED
    (args.output/'runner.json').write_text(json.dumps({'exit_code':code})+'\n')
    raise SystemExit(code)
