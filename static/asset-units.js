/* A selection holds server versions. Failed batches remain selected for review. */
(() => {
  'use strict';
  const core=window.AssetLedger,{api,esc,input,reason,options}=core,$=id=>document.getElementById(id);
  const embedded=new URLSearchParams(location.search).get('view')==='mine';
  const modal=$('asset-unit-drawer'), drawer=embedded?$('asset-mine-units'):modal, selected=new Map();
  if(embedded){drawer.append(...modal.childNodes);modal.remove();drawer.hidden=false;$('asset-unit-close').hidden=true;}
  const labels={normal:'正常',inspection:'待检查',fault:'故障',repair:'维修中',active:'在册',lost:'遗失',retired:'报废',destroyed:'已销毁'};
  const actions={borrow:'借用',return:'归还',handoff:'发起责任移交',accept:'确认接收',cancel_handoff:'取消移交',move:'登记移动',install:'安装',detach:'拆卸',fault:'报障',repair:'送修',healthy:'确认恢复可借',retire:'报废',lost:'遗失结案',recover:'找回／误报恢复',correct:'纠正保管人'};
  let item=null,page=1,rows=[],meta=null,sequence=0,mine=false,canManage=false;
  const btn=(action,label,id='',extra='')=>`<button type="button" class="btn" data-unit-action="${action}" data-id="${id}" ${extra}>${label}</button>`;
  const scanBtn=(task,label)=>`<button type="button" class="btn" data-asset-scan-task="${task}" ${item?`data-item-id="${item.id}"`:''} ${$('asset-unit-campus').value?`data-campus-id="${esc($('asset-unit-campus').value)}"`:''}>${label}</button>`;
  const select=(name,label,values,value='',empty='请选择')=>`<label>${label}<select name="${name}" required>${options(values,value,empty)}</select></label>`;
  const campus=(value='')=>select('campus_id','实际校区',core.catalog.campuses.filter(c=>c.active),value);
  const status=message=>$('asset-unit-status').textContent=message;
  const mayMaintain=u=>canManage||u.keeper_id===core.catalog.member_id;
  const mayBorrow=u=>u.lifecycle==='active'&&!u.keeper_id&&!u.object_id&&u.health==='normal';
  const mayReturn=u=>u.lifecycle==='active'&&u.keeper_id&&mayMaintain(u);
  const mayAccept=u=>u.lifecycle==='active'&&u.receiver_id===core.catalog.member_id;
  function addSelection(unit){
    if(selected.has(unit.id))return;
    if(selected.size>=100)throw Error('每批最多选择 100 件，请先处理或取消已有选择');
    selected.set(unit.id,unit);
  }
  function selectedSummary(){
    const values=[...selected.values()],outside=values.filter(u=>!rows.some(r=>r.id===u.id)).length;
    $('asset-unit-selected').textContent=selected.size?`已选 ${selected.size} 件${outside?` · ${outside} 件不在当前页`:''} · 翻页、切换校区后仍保留`:'单件可直接借还；需要批量操作时，先勾选下方实物。';
    for(const button of $('asset-unit-actions').querySelectorAll('[data-unit-action]')){
      button.disabled=button.dataset.unitAction==='select-page'?!rows.length:!selected.size;
    }
    const summary=$('asset-unit-eligibility');
    if(summary)summary.textContent=selected.size?`按选中时记录：可借 ${values.filter(mayBorrow).length} 件 · 可归还 ${values.filter(mayReturn).length} 件。批量操作会处理全部已选实物，请先核对状态。`:'借用适用于正常、未借出且未安装的实物；归还适用于您可管理的已借出实物。';
    const list=$('asset-unit-selected-list');
    if(list)list.innerHTML=values.map(u=>`<li><span>${esc(u.item_name||'实物')} · U:${u.id}</span>${btn('remove','取消选择',u.id,`aria-label="取消选择 U:${u.id}"`)}</li>`).join('');
    for(const checkbox of drawer.querySelectorAll('[data-unit-select]'))checkbox.checked=selected.has(Number(checkbox.dataset.unitSelect));
  }
  function renderActions(){
    const group=(title,hint,keys)=>`<details class="asset-unit-action-group"><summary>${title}</summary><p class="asset-muted">${hint}</p><div class="asset-actions">${keys.map(a=>btn(a,actions[a])).join('')}</div></details>`;
    $('asset-unit-actions').className='asset-unit-toolbar';
    $('asset-unit-actions').innerHTML=`<div class="asset-unit-primary-actions"><strong>批量操作</strong><div class="asset-actions">${['borrow','return','accept'].map(a=>btn(a,actions[a])).join('')}</div><p id="asset-unit-eligibility" class="asset-muted"></p></div>`+
      `<div class="asset-unit-action-groups">${group('移交责任','交给他人后，由接收人确认；确认前仍由当前保管人负责。',['handoff','cancel_handoff'])}${group('位置与安装','登记存放位置或安装关系；已安装的设备移动前请先拆卸，整机移动请在使用对象中登记。',['move','install','detach'])}${group(canManage?'报障、维护与结案':'故障登记',canManage?'故障与维修单独登记；报废、遗失将结束借用并退出在册。':'发现损坏时先报障，设备将进入待检查状态。',canManage?['fault','repair','healthy','retire','lost','recover','correct']:['fault'])}</div>`+
      `<div class="asset-unit-selection-tools">${btn('select-page','选择本页')}${btn('print','打印已选标签')}${btn('clear','清空全部选择')}<details><summary>查看已选清单</summary><ul id="asset-unit-selected-list"></ul></details></div>`;
  }
  function renderUnit(u){
    const quick=mayAccept(u)?'accept':mayBorrow(u)?'borrow':mayReturn(u)?'return':null;
    const receiver=meta.members.find(m=>m.id===u.receiver_id);
    const state=mayBorrow(u)?'可借用':u.lifecycle!=='active'?labels[u.lifecycle]:u.object_id?'已安装':u.keeper_id?'借出中':labels[u.health];
    return `<article class="asset-card asset-unit-row"><label class="asset-unit-check"><input type="checkbox" data-unit-select="${u.id}" ${selected.has(u.id)?'checked':''} aria-label="选择 ${esc(u.item_name)} U:${u.id}"><span>选择</span></label><div class="asset-unit-identity"><h3>${esc(u.item_name)} <span class="asset-muted">U:${u.id}</span></h3><p>${esc(u.serial?'SN '+u.serial:'未填写厂家 SN')}</p><p class="asset-unit-meta">${esc(u.campus_name)} · ${esc(u.location||'未填写存放位置')}</p></div><div class="asset-unit-meta"><strong>${esc(state||'待核对')}</strong><p>${esc(u.keeper_name||'仓库保管')} · ${esc(labels[u.health]||u.health)}</p>${u.object_id?`<p>安装于 ${esc(u.object_name)} ${esc(u.position)}</p>`:''}${u.receiver_id?`<p>待 ${esc(receiver?.name||'成员 #'+u.receiver_id)} 接收</p>`:''}</div><div class="asset-actions">${quick?btn(quick,actions[quick],u.id,'data-unit-quick="true"'):''}${btn('history','查看记录',u.id)}</div></article>`;
  }
  async function show(id=null,unitId=null,own=false){
    if(embedded&&id){location.href='/assets/detail?id='+encodeURIComponent(id);return;}
    const scanner=window.AssetScan?.dialog;
    if(scanner?.open){
      const closed=new Promise(resolve=>scanner.addEventListener('close',resolve,{once:true}));
      scanner.close();await closed;
    }
    item=!embedded&&id?(await api('items/'+id)).item:null;mine=embedded||own;page=1;$('asset-unit-history').replaceChildren();
    if(!embedded&&!drawer.open)core.showDialog(drawer);
    if(embedded)document.querySelector('[data-asset-mine-view="units"]')?.click();
    await refresh();
    if(unitId){const u=rows.find(r=>r.id===unitId)||(await api('scan/resolve',{raw:'U:'+unitId})).unit;addSelection(u);selectedSummary();}
  }
  async function showRecord(itemId,unitId){
    if(!Number.isSafeInteger(unitId)||unitId<1)throw Error('请提供有效的实物编号');
    await show(embedded?null:itemId);
    // History resolves the exact identity on the server, even outside the visible page.
    return act('history',unitId);
  }
  async function refresh(){
    const seq=++sequence;drawer.setAttribute('aria-busy','true');status('正在读取实物记录…');$('asset-unit-content').inert=true;$('asset-unit-actions').inert=true;
    try{
      meta=await api('units/catalog');
      if(seq!==sequence)return;
      if(item){item=(await api('items/'+item.id)).item;}
      $('asset-unit-title').textContent=item?item.name+' · 单件管理':mine?'我的单件与待接收':'单件工作台';
      if(item&&item.tracking!=='unit'){await migration();return;}
      const data=await api('units?'+new URLSearchParams({page,item_id:item?.id||'',mine:mine?'1':'',campus_id:$('asset-unit-campus').value}));
      if(seq!==sequence)return;
      rows=data.units;
      canManage=data.can_manage;
      $('asset-unit-campus').innerHTML=options(core.catalog.campuses,$('asset-unit-campus').value,'全部校区');
      $('asset-unit-content').innerHTML=`<div class="asset-heading asset-unit-list-heading"><div><h3>实物清单</h3>${item?`<p class="asset-muted"><a href="/assets/detail?id=${item.id}">查看物料详情与全部流水</a> · 每件实物单独追踪</p>`:'<p class="asset-muted">行内借还只操作当前实物，批量操作只处理已选清单。</p>'}</div>${data.can_manage&&item?`<div class="asset-actions">${scanBtn('receive','扫码收货')}${btn('receive','手工登记实物')}</div>`:''}</div>`+
        `<div class="asset-actions" aria-label="扫码办理实物业务">${mine?'':scanBtn('borrow','扫码借用')}${scanBtn('return','扫码归还')}${scanBtn('move','扫码移动')}</div>`+
        (rows.length?'<div class="asset-unit-list">'+rows.map(renderUnit).join('')+'</div>':`<div class="asset-empty"><h3>${mine?'暂无借用或待接收实物':'当前校区暂无实物记录'}</h3><p>${mine?'借用或收到移交后，实物会出现在这里。':data.can_manage&&item?'新设备到货后，点击「新增实物 / 批量建档」登记。':'可切换校区查看其他实物。'}</p></div>`)+
        `<div class="asset-pager">${page>1?btn('prev','上一页'):''}<span>第 ${page} 页 · ${data.total} 件</span>${page*20<data.total?btn('next','下一页'):''}</div>`;
      renderActions();selectedSummary();status('选择操作后可先核对，确认保存才会改变记录。');
    }catch(e){status(e.message);}finally{if(seq===sequence){drawer.setAttribute('aria-busy','false');$('asset-unit-content').inert=false;$('asset-unit-actions').inert=false;}}
  }
  async function migration(){
    $('asset-unit-actions').replaceChildren();
    if(!core.catalog.can_manage){$('asset-unit-content').textContent='此型号尚未完成实物核对，请联系管理人员。';status('旧数量台账保留，不自动分配单件身份。');return;}
    const d=await api('units/migration/'+item.id);
    const stocks=d.stock||d.stocks||[];
    $('asset-unit-content').innerHTML=`<p>逐型号核对实物与未还借单。冻结期间不允许数量操作。没有厂家 SN 时可留空；每一行仍会产生不同身份。</p>`+
      `<h3>各校区在库</h3><ul>${stocks.map(s=>`<li>校区 #${s.campus_id}：${s.available}</li>`).join('')}</ul>`+
      `<h3>未还借单</h3><ul>${d.loans.map(l=>`<li>#${l.id} ${esc(l.member_name)} · 校区 #${l.campus_id} · ${l.outstanding} 件</li>`).join('')}</ul>`+
      `<p>已人工核对 ${d.entries.length} 件</p><ul>${d.entries.map(e=>`<li>校区 #${e.campus_id} · ${e.loan_id?'对应借单 #'+e.loan_id:'仓库在库'} · ${esc(e.serial||'无厂家 SN')}</li>`).join('')}</ul>`+
      '<div class="asset-actions">'+(['legacy','quantity'].includes(item.tracking)?btn('migrate-start','冻结并开始核对'):btn('migrate-add','登记核对实物')+btn('migrate-finish','确认全部对应并切换')+btn('migrate-cancel','取消核对并解冻'))+'</div>';
    status(item.tracking==='migrating'?'型号已冻结；误录可取消本次核对后重新登记。':'尚未分配任何单件身份。');
  }
  async function act(action,id,historyPage=1){
    if(action==='prev'||action==='next'){page+=action==='next'?1:-1;return refresh();}
    if(action==='clear'){selected.clear();selectedSummary();return;}
    if(action==='remove'){selected.delete(id);selectedSummary();return;}
    if(action==='select-page'){
      if(new Set([...selected.keys(),...rows.map(u=>u.id)]).size>100)throw Error('选择本页后将超过 100 件，请先处理或取消已有选择');
      rows.forEach(addSelection);selectedSummary();return;
    }
    if(action==='history-close'){$('asset-unit-history').replaceChildren();drawer.querySelector(`[data-unit-action="history"][data-id="${id}"]`)?.focus();return;}
    if(action==='history'){
      const h=await api(`units/${id}/history?page=${historyPage}`);
      const history=$('asset-unit-history');history.className='asset-unit-history';history.tabIndex=-1;
      history.innerHTML=`<header class="asset-heading"><div><h3>U:${id} · 流转记录</h3><p class="asset-muted">第 ${h.page} 页 / 共 ${h.total} 条</p></div>${btn('history-close','收起记录',id)}</header>${h.events.length?`<ol class="asset-log">${h.events.map(e=>`<li>#${e.id} ${esc(actions[e.action.replace('unit_','')]||e.action)} · ${esc(e.actor_name)} · ${esc(e.created_at)}<p>${esc(e.reason)}</p><details><summary>前后状态与来源</summary><pre>${esc(JSON.stringify({details:e.details,receipt:e.receipt},null,2))}</pre></details></li>`).join('')}</ol>`:'<p class="asset-muted">暂无流转记录。</p>'}`;
      const pager=document.createElement('nav');pager.className='asset-pager';pager.setAttribute('aria-label','实物流转记录分页');
      for(const [label,p] of [['上一页',h.page-1],['下一页',h.page+1]])if(p>0&&(p-1)*100<h.total){const b=document.createElement('button');b.className='btn';b.textContent=label;b.onclick=()=>act('history',id,p).catch(e=>status(e.message));pager.append(b);}
      if(pager.childElementCount)history.append(pager);
      history.focus({preventScroll:true});history.scrollIntoView({block:'start',behavior:'smooth'});return;
    }
    if(action==='print'){if(!selected.size)throw Error('请先选择需要打印的实物');return window.AssetLabels?.open([...selected.values()]);}
    if(action.startsWith('migrate-')){
      const mode=action.slice(8);let fields='';
      if(mode==='add'){
        const d=await api('units/migration/'+item.id);
        fields=campus()+select('loan_id','归属（必须与实物核对一致）',[{id:0,name:'仓库在库'},...d.loans.map(l=>({id:l.id,name:`借单 #${l.id} ${l.member_name} 未还 ${l.outstanding}`}))],0,'')+input('quantity','核对件数',1,'type="number" min="1" max="100" required')+'<label>厂家 SN（选填，一件一行）<textarea name="serials" rows="3"></textarea></label>';
      }
      core.open(({start:'开始核对旧库存',add:'登记核对实物',finish:'确认切换单件管理',cancel:'取消核对并解冻'})[mode]+' · '+item.name,fields+reason(true),'units/migrate',{item_id:item.id,action:mode,version:item.version},['campus_id','quantity','loan_id']);return;
    }
    if(action==='receive'){
      core.open('新增实物 · '+item.name,campus()+input('quantity','实收件数',1,'type="number" min="1" max="100" required')+'<label>厂家 SN（选填，一件一行，行数须等于件数）<textarea name="serials" rows="3"></textarea></label>'+reason(true),'units/receive',{item_id:item.id},['quantity','campus_id']);return;
    }
    const units=id?[rows.find(u=>u.id===id)]:[...selected.values()];
    if(!units.length||units.some(u=>!u))throw Error('请先选择单件；跨页选择会保留');
    return operate(action,units,{source:id?'single':'selection'});
  }
  async function operate(action,values,context={}){
    if(!Object.hasOwn(actions,action))throw Error('请选择有效的单件操作');
    if(!Array.isArray(values)||!values.length||values.length>100)throw Error('每次请核对 1 至 100 件实物');
    if(values.some(u=>!u||!Number.isInteger(u.id)||u.id<1||!Number.isInteger(u.version)||u.version<0)||new Set(values.map(u=>u.id)).size!==values.length)throw Error('实物清单重复或缺少版本，请重新扫码核对');
    const units=values.map(u=>({...u}));
    if(!core.catalog)throw Error('物料资料尚未读取完成，请稍后重试');
    if(!meta)meta=await api('units/catalog');
    const scope=context.source==='selection'?'本次处理全部已选实物；任一实物不符合条件时，整批保留，您可以返回重新选择。':context.source==='single'?'仅操作此实物，其他已选实物不受影响。':'仅处理本次扫码清单，不包含单件工作台中其他已选实物；取消或失败后可返回重新核对。';
    let fields=`<div class="asset-operation-summary"><strong>${units.length===1?esc(units[0].item_name||'实物')+' · U:'+units[0].id:'本次共 '+units.length+' 件实物'}</strong><p>${units.map(u=>'U:'+u.id).join('、')}</p><p class="asset-muted">${scope}</p></div>`;
    if(['borrow','handoff','correct'].includes(action)){
      if(action!=='borrow'||core.catalog.can_manage)fields+=select('member_id',action==='handoff'?'接收人（确认后才转移责任）':action==='borrow'?'借用人':'纠正后的保管人',action==='correct'?[{id:0,name:'仓库，无个人保管人'},...meta.members]:meta.members,action==='borrow'?core.catalog.member_id:'');
    }
    if(['return','move'].includes(action))fields+=campus(action==='return'?context.campusId||'':'');
    if(action==='return')fields+=select('health','归还状况',[{id:'normal',name:'正常'},{id:'inspection',name:'损坏／待检查'}],'normal','');
    if(['return','repair','lost','retire'].includes(action)&&units.some(u=>u.object_id))fields+=select('detached','确认已经拆下',[{id:'true',name:'已拆下，记录拆卸后继续'}]);
    if(action==='install')fields+=select('object_id','机器人／测试平台',meta.objects.filter(o=>o.active))+input('position','安装位置','','maxlength="120"');
    if(['move','detach','install'].includes(action))fields+=input('location','实际存放位置','','required maxlength="120"');
    if(action==='retire')fields+=select('destroyed','是否已实际销毁',[{id:'false',name:'未销毁（误报可恢复）'},{id:'true',name:'已销毁（不可恢复）'}],'false','');
    if(action==='recover')fields+='<p>恢复原身份并进入待检查，不重开旧借单。</p>';
    core.open(actions[action]+' · '+(units.length===1?units[0].item_name||'U:'+units[0].id:units.length+' 件实物'),fields+reason(true),'units/operate',{action,units:units.map(u=>({id:u.id,version:u.version}))},['member_id','campus_id','object_id']);
  }
  drawer.addEventListener('click',e=>{const b=e.target.closest('[data-unit-action]');if(b)act(b.dataset.unitAction,Number(b.dataset.id)).catch(e=>status(e.message));});
  drawer.addEventListener('change',e=>{if(e.target.dataset.unitSelect){const u=rows.find(v=>v.id===Number(e.target.dataset.unitSelect));try{if(e.target.checked)addSelection(u);else selected.delete(u.id);}catch(error){status(error.message);}selectedSummary();}});
  $('asset-unit-close').onclick=()=>drawer.close();
  $('asset-unit-campus').onchange=()=>{page=1;refresh();};
  document.addEventListener('click',e=>{const a=e.target.closest('[data-unit-model]');if(a){e.preventDefault();show(Number(a.dataset.unitModel)).catch(e=>status(e.message));}});
  document.addEventListener('assets-loaded',()=>{if(!embedded&&drawer.open)refresh();});
  document.addEventListener('asset-operation-saved',e=>{
    if(e.detail.path==='units/operate'){
      for(const u of e.detail.body.units)if(selected.get(u.id)?.version===u.version)selected.delete(u.id);
      selectedSummary();if(!embedded)refresh();
    }
  });
  window.AssetUnits={show,showRecord,refresh,operate,scanOperate:(action,units,context={})=>operate(action,units,{...context,source:'scan'}),loadMine:()=>{item=null;mine=true;return refresh();},select:async unit=>{if(selected.has(unit.id))throw Error('此单件已在待提交清单中');addSelection(unit);selectedSummary();},get selection(){return [...selected.values()];}};
})();
