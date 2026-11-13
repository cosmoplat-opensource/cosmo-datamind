/* Context identity, legacy reports and stage-aware suggestions — DR-072. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({console});context.window=context;
context.RoboticsAssets={escape:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
for(const file of ['robotics-application.js','robotics-design.js'])vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../ui/modules',file),'utf8'),context);
const app=context.RoboticsApplication,design=context.RoboticsDesign;
const gap=(kind,role)=>({code:'schema.missing_role',layer:'schema',message:'缺少明确的应用角色对应。',
 expected:{kind,role,label:'合成标签',...(kind==='object_properties'?{domain_ids:['SkillInvocation'],range_ids:['ParameterBinding']}:{}),...(kind==='data_properties'?{datatype:'xsd:string'}:{})},
 repair_actions:[{action_id:'robotics.revise_tbox',target:{resource_id:'s1',expected_revision:10}}]});
const report=()=>({report:{application_id:'robotics.pick_place',entrypoint:'robotics.generate_behavior_tree',check_id:'c1',
 issues:[gap('classes','ParameterBinding'),gap('object_properties','hasBinding'),gap('data_properties','literalValue')]},freshness:'current'});
const cases={
 legacy_names(){const issue={code:'schema.missing_role',message:'缺少明确的应用角色对应。',expected:'旧报告',entity_refs:['urn:example#ImplementationConfiguration']};
  assert.ok(app.issueText(issue).includes('ImplementationConfiguration'));
  assert.ok(app.renderIssue(issue).includes('ImplementationConfiguration'));},
 context_identity(){const response=report();response.report.issues.push({...gap('classes','Other'),repair_actions:[{action_id:'robotics.revise_tbox',target:{resource_id:'other'}}]});
  const result=design.repairContext(response,'s1');assert.equal(result.items.length,3);assert.equal(result.revision,10);
  assert.throws(()=>design.repairContext(response,'unrelated'),/不属于当前设计/);
  response.report.application_id='data.deep_qa';assert.throws(()=>design.repairContext(response,'s1'),/不属于当前设计/);},
 stages(){const hints=design.repairContext(report(),'s1');const classes=design.repairSuggestion(hints,'classes'),properties=design.repairSuggestion(hints,'properties');
  assert.ok(classes.includes('本轮仅修订类别'));assert.ok(classes.includes('ParameterBinding'));
  assert.ok(classes.includes('关系与属性在类别确认后处理'));assert.ok(!classes.includes('hasBinding'));
  assert.ok(properties.includes('保持已确认类别不变'));assert.ok(properties.includes('hasBinding'));
  assert.ok(properties.includes('literalValue'));
  assert.ok(properties.includes('不编造实例'));},
 escaped_html(){const issue=gap('classes','<img src=x onerror=alert(1)>');issue.message='<script>alert(1)</script>';
  const html=app.renderIssue(issue);assert.ok(!html.includes('<img')&&!html.includes('<script>'));assert.ok(html.includes('&lt;'));},
 historical(){const response=report();response.freshness='stale';const hints=design.repairContext(response,'s1');
  assert.equal(hints.freshness,'stale');assert.equal(hints.revision,10);assert.ok(design.repairSuggestion(hints,'classes').includes('此前应用检查'));},
 async explicit_stage_change(){
  const calls=[],reply={href:'#build?session=s1',target:{resource_kind:'design_session',resource_id:'s1'},
   design_repair:{session_id:'s1',expected_revision:10,current_stage:'abox',stage:'properties'}};
  const fetch=async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return new Response(JSON.stringify({session_id:'s1',stage:'properties'}));};
  assert.equal(await design.beginRepair(reply,'click-1',fetch),reply.href);
  assert.equal(calls.length,1);assert.ok(calls[0].url.endsWith('/s1/edit-properties'));
  assert.deepEqual(calls[0].body,{request_id:'repair-stage-click-1',expected_revision:10});
  calls.length=0;reply.design_repair.current_stage='properties';
  await design.beginRepair(reply,'click-1',fetch);assert.equal(calls.length,0);
  reply.design_repair.stage='unknown';await assert.rejects(()=>design.beginRepair(reply,'click-2',fetch),/目标/);
  assert.equal(calls.length,0);
 },
 adaptation(){const data={origin_session:{id:'s1',revision:4},issues:[gap('classes','ParameterBinding')]};
  assert.equal(design.repairContext(data,'s1',true).items.length,1);
  assert.throws(()=>design.repairContext(data,'s2',true),/不属于当前设计/);}
};
assert.ok(cases[process.argv[2]],'Unknown test case');cases[process.argv[2]]();
