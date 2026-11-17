/* Shared structured QA issue presentation — DR-156. */
(function(global){
// DR-156: actionable structured QA failures; existing navigation keeps the selected scope.
function dqContractErrorHTML(ev){
 const issues=ev.application_check?.issues||[];
 const connection=issues.some(i=>i.code==='data.table_unavailable');
 const binding=issues.some(i=>i.code==='data.table_unbound');
 return `<div role="alert"><span class="dq-t warn">${esc(ev.error||'查询未完成，请重试')}</span>${issues.length?`<details style="margin-top:12px"><summary>查看问题和处理建议</summary>${issues.map(i=>`<div class="step"><p>${esc(i.message)}</p><small>${esc((i.entity_refs||[]).join('、'))}</small></div>`).join('')}</details>`:''}<div style="margin-top:12px">${connection?'<a href="#conn">检查数据连接 →</a>':''}${binding?'<a href="#review" onclick="goReview(DQ_DS.graphs[0]||&quot;demo&quot;);return false">检查对象与表绑定 →</a>':''}</div></div>`;
}

global.dqContractErrorHTML=dqContractErrorHTML;
})(window);
