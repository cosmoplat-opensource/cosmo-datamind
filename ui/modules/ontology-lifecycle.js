/* Reversible lifecycle confirmation and recycle bin actions — DR-157. */
(function(global){
 'use strict';
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function json(url,body){
  const response=await global.fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let data;try{data=await response.json();}catch(_){throw new Error('响应格式无效，请刷新后重试。');}
  if(!response.ok)throw new Error(data.error?.message||data.error||'操作未完成，请刷新预览。');
  return data;
 }
 let modal=null;
 async function open(item,onDone){
  modal?.remove();
  const operation=item.lifecycle?.state==='deleted'?'restore':'delete',oid=item.ontology_id;
  const base='/api/ontology-assets/'+encodeURIComponent(oid),label=operation==='delete'?'移到回收站':'恢复',title=operation==='delete'?'移到回收站？':'恢复本体？';
  const dialog=global.document.createElement('div');modal=dialog;
  dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',label+'本体');
  dialog.style.cssText='position:fixed;inset:0;z-index:10000;background:#14213d66;display:flex;align-items:center;justify-content:center;padding:16px';
  dialog.innerHTML=`<section class="card ol-dialog"><h3>${title}</h3><p class="ol-name">${esc(item.name||item.asset?.name||'当前本体')}</p><p class="ol-note">${operation==='delete'?'可恢复，历史记录和文件保留。':'恢复后需重新检查，旧任务不会自动继续。'}</p><div class="ol-impact" data-preview role="status">正在检查…</div><p class="ol-error" data-error role="alert"></p><div class="ol-actions"><button type="button" data-cancel class="ghost">取消</button><button type="button" data-refresh class="ghost" hidden>重新检查</button><button type="button" data-commit disabled>${label}</button></div></section>`;
  global.document.body.append(dialog);
  let preview=null,rid=global.dmRequestId(),busy=false;
  const alive=()=>modal===dialog&&dialog.isConnected;
  const dismiss=()=>{if(!busy){dialog.remove();if(modal===dialog)modal=null;}};
  dialog.querySelector('[data-cancel]').onclick=dismiss;
  // 键盘:Esc 关闭(提交中不关);Tab 在对话框内循环,焦点不落到背后的页面
  dialog.addEventListener('keydown',event=>{
   if(event.key==='Escape'){event.preventDefault();dismiss();return;}
   if(event.key!=='Tab')return;
   const items=[...dialog.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(x=>!x.disabled&&!x.hidden);
   if(!items.length)return;
   const first=items[0],last=items[items.length-1];
   if(event.shiftKey&&global.document.activeElement===first){event.preventDefault();last.focus();}
   else if(!event.shiftKey&&global.document.activeElement===last){event.preventDefault();first.focus();}
  });
  const refresh=async()=>{
   if(busy)return;preview=null;dialog.querySelector('[data-commit]').disabled=true;
   dialog.querySelector('[data-error]').textContent='';dialog.querySelector('[data-refresh]').hidden=true;rid=global.dmRequestId();
   try{
    const value=await json(base+'/'+(operation==='delete'?'deletion-preview':'restoration-preview'),{});
    if(!alive())return;preview=value;
    const counts=value.impact_totals||Object.fromEntries(Object.entries(value.impact||{}).map(([k,v])=>[k,v.length]));
    const summary=esc(counts.version_ids||0)+' 个版本 · '+esc(counts.sessions||0)+' 个对话';
    dialog.querySelector('[data-preview]').innerHTML=`<p>${summary}</p>${value.protected?'<p>内置本体不可移除。</p>':''}${value.blockers?.some(b=>b.code==='asset_busy')?'<p>任务尚未结束，暂时无法移除。</p>':''}`;
    dialog.querySelector('[data-commit]').disabled=!value.can_apply;
    dialog.querySelector('[data-refresh]').hidden=value.can_apply;
   }catch(error){if(alive()){dialog.querySelector('[data-error]').textContent=error.message;dialog.querySelector('[data-refresh]').hidden=false;}}
  };
  dialog.querySelector('[data-refresh]').onclick=refresh;
  dialog.querySelector('[data-commit]').onclick=async()=>{
   if(busy||!preview?.can_apply)return;busy=true;dialog.querySelector('[data-commit]').dataset.busy='1';
   dialog.querySelector('[data-commit]').disabled=true;dialog.querySelector('[data-refresh]').disabled=true;
   try{
    const result=await json(base+'/'+operation,{expected_revision:preview.revision,impact_sha256:preview.impact_sha256,request_id:rid,confirmed:true});
    if(!alive())return;
    global.dispatchEvent(new CustomEvent('ontology-lifecycle-changed',{detail:{ontology_id:oid,state:result.lifecycle.state,legacy_keys:preview.impact.legacy_keys||[]}}));
    dialog.remove();modal=null;await onDone?.(result);
   }catch(error){if(alive()){dialog.querySelector('[data-error]').textContent=error.message;dialog.querySelector('[data-commit]').disabled=false;dialog.querySelector('[data-refresh]').hidden=false;}}
   finally{busy=false;if(alive()){dialog.querySelector('[data-refresh]').disabled=false;delete dialog.querySelector('[data-commit]').dataset.busy;}}
  };
  await refresh();dialog.querySelector('[data-cancel]')?.focus();
 }
 function attach(host,item,onDone){
  if(!item.ontology_id||!item.lifecycle)return;
  const area=host.querySelector('.ra-card-actions,.ra-detail-actions')||host;
  if(!item.lifecycle.protected){
   const button=global.document.createElement('button');button.type='button';button.className='ghost';
   button.textContent=item.lifecycle.state==='deleted'?'恢复':'移到回收站';button.dataset.lifecycleAction=item.lifecycle.state==='deleted'?'restore':'delete';
   button.onclick=()=>open(item,onDone);area.append(button);
  }
  if(item.lifecycle.state==='deleted'){
   if(!host.classList.contains('ra-asset')){const banner=global.document.createElement('p');banner.className='ol-deleted-note';banner.setAttribute('role','status');banner.textContent='已删除 · 历史可查看，恢复后需重新检查。';host.prepend(banner);}
   host.querySelectorAll('a').forEach(a=>{if(/#robotics-task|#graph=|tab=knowledge|#review|#build\?/.test(a.getAttribute('href')||'')){a.removeAttribute('href');a.setAttribute('aria-disabled','true');}});
  }
 }
 async function legacy(key,onDone){
  const data=await json('/api/ontology-assets?state=all');const item=data.items.find(x=>x.legacy_key===key);
  if(!item)throw new Error('请先完成本体生命周期迁移，再刷新目录。');
  return open(item,onDone);
 }
 // 页面切换时关闭对话框(提交中保留,等结果回来)
 function close(){if(modal&&modal.isConnected&&!modal.querySelector('[data-commit]')?.dataset.busy){modal.remove();modal=null;}}
 global.OntologyLifecycle={open,attach,legacy,close};
})(window);
