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
assert spec is not None and spec.loader is not None
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
browser = support.browser
live = support.live


@pytest.fixture(autouse=True)
def no_page_errors(live):
    yield
    assert live[0].evidence['page_errors'] == []


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
    page.get_by_text('继续提修改意见', exact=True).click()
    page.get_by_label('建模要求', exact=True).fill('尚未发送的文字')
    expect(node).to_have_attribute('transform', before)
    support.click_operation(page, '确认类别', '/confirm-classes')
    support.wait_design(page, sid, 'properties', 'awaiting_input')
    expect(node).to_have_attribute('transform', before)
    before_calls = len(calls)
    panel.get_by_label('设计修订', exact=True).select_option('1')
    expect(panel.locator('.studio-context')).to_contain_text('历史修订 1')
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


def probe(page, site, *, nodes=2, edges=1, instances=0):
    page.goto(site.url + '/#library')
    page.evaluate('''async ({nodes,edges,instances})=>{
      const {mountPanel}=await import('/assets/modules/studio/panel.js');
      const host=document.createElement('div');host.id='studio_probe';document.body.replaceChildren(host);
      const ns='https://probe.test/#';
      const doc={schema:'datamind-studio/1',context:{kind:'version',key:'probe',revision:'fixed',stage:'fixed',status:'candidate'},metadata:{read_only:true},warnings:[],properties:[],relations:[],
        nodes:Array.from({length:nodes},(_,i)=>({id:ns+i,iri:ns+i,type:'class',labels:[{value:'类别 '+i,language:'zh'}],definition:'用于验证图谱呈现',individuals:i===0?Array.from({length:instances},(_,j)=>ns+'i'+j):[]})),
        individuals:Array.from({length:instances},(_,j)=>({id:ns+'i'+j,iri:ns+'i'+j,types:[ns+'0'],labels:[{value:'实例 '+j}],data:[]})),
        edges:Array.from({length:edges},(_,i)=>({id:'edge'+i,source:ns+(i%nodes),target:ns+((i+1)%nodes),predicate:ns+'关联',kind:'object',labels:[{value:'关联'}]}))};
      const panel=mountPanel(host);panel.present(doc);window.studioProbe={panel,doc,mountPanel,host};
    }''', {'nodes': nodes, 'edges': edges, 'instances': instances})
    return page.locator('#studio_probe')


def test_force_drag_pin_search_zoom_and_keyboard_details(live):
    site, page, calls = live
    panel = probe(page, site, nodes=2)
    page.evaluate('''()=>{const c=studioProbe.panel.controller;c.pause(true);
      Object.assign(c.graph.positions.get('https://probe.test/#0'),{x:-100,y:0,vx:0,vy:0});
      Object.assign(c.graph.positions.get('https://probe.test/#1'),{x:100,y:0,vx:0,vy:0});
      c.graph.restoreView({...c.graph.getView(),transform:{x:0,y:0,k:1},paused:true});c.pause(false);
    }''')
    # Capture the real pointer-to-next-frame delay; rendering remains unmodified.
    page.evaluate('''()=>{window.pointerDelay=null;document.querySelector('#studio_probe svg').addEventListener('pointermove',()=>{
      const start=performance.now();requestAnimationFrame(()=>{window.pointerDelay=performance.now()-start;});});}''')
    node = panel.locator('[data-node-id="https://probe.test/#0"]')
    box = node.bounding_box(); assert box
    before = page.evaluate("studioProbe.panel.controller.graph.positions.get('https://probe.test/#1').x")
    page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
    page.mouse.down();page.mouse.move(box['x']+box['width']/2-100,box['y']+box['height']/2,steps=5);page.mouse.up()
    page.wait_for_timeout(800)
    after = page.evaluate("studioProbe.panel.controller.graph.positions.get('https://probe.test/#1').x")
    assert abs(after-before)>=2
    assert page.evaluate('window.pointerDelay')<=100
    node.focus();page.keyboard.press('Enter')
    expect(panel.locator('.studio-details h3')).to_have_text('类别 0')
    fixed=page.evaluate("studioProbe.panel.controller.graph.positions.get('https://probe.test/#0').x")
    page.wait_for_timeout(200)
    assert abs(page.evaluate("studioProbe.panel.controller.graph.positions.get('https://probe.test/#0').x")-fixed)<=.5
    panel.get_by_role('button',name='释放节点',exact=True).click()
    panel.get_by_label('查找节点',exact=True).fill('类别 1');panel.get_by_role('button',name='查找',exact=True).click()
    expect(panel.locator('.studio-details h3')).to_have_text('类别 1')
    zoom=page.evaluate('studioProbe.panel.controller.graph.transform.k')
    panel.get_by_role('button',name='放大',exact=True).click()
    assert page.evaluate('studioProbe.panel.controller.graph.transform.k')>zoom
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
    expect(panel.locator('.studio-details')).to_contain_text('已显示 50 / 1000')
    panel.get_by_role('button',name='继续显示实例',exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(102)
    expect(panel.locator('.studio-details')).to_contain_text('已显示 100 / 1000')
    panel.get_by_role('button',name='下一页实例',exact=True).click()
    expect(panel.locator('svg [data-node-id]')).to_have_count(52)
    expect(panel.locator('.studio-details')).to_contain_text('范围 101～150')
    for _ in range(17):
        panel.get_by_role('button',name='下一页实例',exact=True).click()
    expect(panel.locator('.studio-details')).to_contain_text('范围 951～1000')
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
      const {panel,doc,mountPanel,host}=studioProbe;
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
    site.evidence['studio_performance']=measured
    assert calls==[]


def test_late_fixed_result_during_history_switch_is_cached_without_overwriting_history(live):
    site,page,calls=live
    admitted=support.ui_admit(page,site);sid=admitted['session_id']
    support.wait_design(page,sid,'classes','awaiting_confirmation');panel=studio(page)
    support.click_operation(page,'确认类别','/confirm-classes')
    support.wait_design(page,sid,'properties','awaiting_input')
    page.get_by_role('button',name='开始关系与属性设计',exact=True).click()
    support.wait_design(page,sid,'properties','awaiting_confirmation')
    # Delay delivery of a genuine server response; never replace its bytes or status.
    page.evaluate('''()=>{const original=window.fetch;window.fetch=async(...args)=>{
      const response=await original(...args);
      if(String(args[0]).endsWith('/views/studio'))await new Promise(resolve=>{window.releaseStudioResult=resolve;});
      return response;
    };}''')
    support.click_operation(page,'确认骨架并构建实例','/confirm-schema')
    support.wait_design(page,sid,'abox','completed')
    page.wait_for_function('typeof window.releaseStudioResult === "function"')
    panel.get_by_label('设计修订',exact=True).select_option('1')
    expect(panel.locator('.studio-context')).to_contain_text('历史修订 1')
    page.evaluate('window.releaseStudioResult()')
    page.wait_for_function('!!document.querySelector("[data-testid=studio-panel]").studioResult')
    expect(panel.locator('.studio-context')).to_contain_text('历史修订 1')
    panel.get_by_label('设计修订',exact=True).select_option('current')
    expect(panel).to_have_attribute('data-kind','version')
    assert len(calls)==2


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--output', type=Path, default=ROOT / '.check-results/studio-browser')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT']=str(args.output.resolve())
    code=pytest.main([__file__,'-q','--tb=short','-o','addopts=','--junitxml='+str(args.output/'results.xml')])
    (args.output/'runner.json').write_text(json.dumps({'exit_code':code})+'\n')
    raise SystemExit(code)
