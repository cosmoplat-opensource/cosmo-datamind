/* 动作中心的动作记录:列表与导出共用同一筛选(DR-161),导出内容与屏幕一致。 */
function acLogRows(){const st=($('#acf_status')&&$('#acf_status').value)||'';
 const kw=(($('#acf_kw')&&$('#acf_kw').value)||'').trim();
 return AC_LOG.filter(x=>(!st||x.status===st)&&(!kw||JSON.stringify([x.action_cn,x.operator,x.approver,x.params]).includes(kw)));}
function acLogCsv(){const items=acLogRows();if(!items.length){toast('当前筛选无记录',false);return;}
 const esc2=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';
 const csv='﻿'+['时间,动作,状态,操作人,参数,审批人,审批意见,审批时间,效果']
  .concat(items.map(x=>[x.ts,x.action_cn,x.status,x.operator,Object.entries(x.params||{}).map(([k,v])=>k+':'+v).join(';'),
   x.approver||'',x.approve_comment||'',x.approve_ts||'',(x.effects||[]).join(';')].map(esc2).join(','))).join('\n');
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
 a.download='动作审计_'+new Date().toISOString().slice(0,10)+'.csv';a.click();URL.revokeObjectURL(a.href);}

Object.assign(window, {acLogRows, acLogCsv});
