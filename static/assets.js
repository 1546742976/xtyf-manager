/* One backend ledger for browser and packaged client pages. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), esc=window.BusinessUI.escape;
  const params=new URLSearchParams(location.search), itemId=Number(params.get('id'))||null;
  const mine=params.get('view')==='mine';
  const validAssetViews=['all','equipment','quantity','supply','attention'];
  let assetView=validAssetViews.includes(params.get('type'))?params.get('type'):'all';
  const names={receive:'入库',borrow:'借用',return:'归还',consume:'领用',adjust:'盘点调整',transfer:'校区调拨',correct:'纠错',catalog:'目录维护',supply_status:'耗材状态'};
  let catalog, detail, listing=[], loans=[], events=[], generation=0, listPage=1, loanPage=1, eventPage=1, eventCampus='',detailView='stock',mineView='units';
  let operation=null, pending=false, opener=null, serverReceivedAt=0;
  const pageSizes={items:20,loans:20,events:20}, pagerHandlers=new Map(),categorySelection=new Map();
  let organizing=false;
  // In-flight writes survive dialog closure, page reloads and packaged-view navigation.
  // Namespace by server, then verify the user before ever replaying a saved write.
  const serverScope=window.XTYF?.config?.serverUrl||location.origin;
  const pendingKey='xtyf.assets.unconfirmed.v1:'+serverScope;
  function readPending(){
    try{
      const value=JSON.parse(sessionStorage.getItem(pendingKey)||'null');
      return value&&typeof value==='object'&&value.request_id&&value.path&&value.body?value:null;
    }catch(_){return null;}
  }
  let unresolved=readPending();
  function showPending(){
    const host=$('asset-pending'),retry=$('asset-pending-retry');
    host.hidden=!unresolved;
    retry.disabled=!!pending;
    if(!unresolved)return;
    const sameUser=catalog&&unresolved.actor_id===catalog.member_id;
    $('asset-pending-message').textContent=sameUser?
      '上一次提交结果尚未确认。新登记已暂停，请按原请求重试；服务器会识别重复请求。':
      '存在其他账号尚未确认的登记。为防止误记到当前账号，请先使用原账号核对。';
    retry.hidden=!sameUser;
  }
  function rememberPending(value){
    sessionStorage.setItem(pendingKey,JSON.stringify(value));
    unresolved=value;showPending();
  }
  function forgetPending(){
    sessionStorage.removeItem(pendingKey);
    unresolved=null;showPending();
  }
  const dialog=$('asset-dialog'), form=$('asset-form'), filter=$('asset-filter');
  const dialogOrigins=new WeakMap(),suspendedDialogs=new WeakSet();
  function showDialog(modal){
    if(modal.open)return;
    const parent=[...document.querySelectorAll('dialog.asset-dialog[open]')].at(-1)||null;
    dialogOrigins.set(modal,{parent,focus:document.activeElement});
    if(parent){suspendedDialogs.add(parent);parent.close();}
    const header=modal.querySelector('header');
    let context=header.querySelector('.asset-dialog-context');
    if(!context){context=document.createElement('p');context.className='asset-dialog-context';header.prepend(context);}
    context.textContent=parent?`物料管理 / ${parent.querySelector('h2')?.textContent||'上一步'}`:'物料管理';
    const back=header.querySelector('button');
    if(back){back.dataset.closeLabel||=back.textContent;back.textContent=parent?'← 返回':back.dataset.closeLabel;}
    if(modal===dialog)$('asset-form-cancel').textContent=parent?'取消并返回':'取消';
    modal.showModal();
  }
  // Keep scrolling inside the rounded shell; only a complete backdrop click dismisses.
  for(const modal of document.querySelectorAll('dialog.asset-dialog')){
    const scroll=document.createElement('div');scroll.className='asset-dialog-scroll';
    scroll.append(...modal.childNodes);modal.append(scroll);
    modal.addEventListener('close',()=>{
      if(suspendedDialogs.delete(modal))return;
      const origin=dialogOrigins.get(modal);dialogOrigins.delete(modal);
      if(!origin)return;
      if(origin.parent?.isConnected&&!origin.parent.open&&!document.querySelector('dialog.asset-dialog[open]')){
        origin.parent.showModal();
        origin.parent.dispatchEvent(new CustomEvent('asset-dialog-resumed'));
      }
      if(origin.focus?.isConnected&&origin.focus.getClientRects().length)origin.focus.focus();
    });
    modal.addEventListener('cancel',e=>{if(pending)e.preventDefault();});
    const outside=e=>{const r=modal.getBoundingClientRect();return e.target===modal&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
    let beganOutside=false;
    modal.addEventListener('pointerdown',e=>{beganOutside=outside(e);});
    modal.addEventListener('click',e=>{
      if(beganOutside&&outside(e)&&!pending)modal.querySelector('header button')?.click();
      beganOutside=false;
    });
  }
  const requestId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
  const status=(message,state='info')=>{$('asset-status').textContent=message;$('asset-status').dataset.state=state;};
  async function api(path, body){
    const response=await fetch('/api/assets/'+path, body?{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    }:{});
    const data=await response.json().catch(()=>({error:'服务器返回了无法识别的内容'}));
    if(!response.ok||!data.ok){const error=new Error(data.error||'请求失败');error.status=response.status;error.data=data;throw error;}
    return data;
  }
  const button=(action,label,id,extra='')=>`<button type="button" class="btn" data-action="${action}" data-id="${id}" ${extra}>${label}</button>`;
  function pager(name,page,total,perPage=20){
    const last=Math.max(1,Math.ceil(total/perPage));page=Math.max(1,Math.min(page,last));
    const control=(label,target,disabled=false,extra='')=>`<button type="button" class="btn" data-asset-page="${target}" ${disabled?'disabled':''} ${extra}>${label}</button>`;
    if(!catalog?.intake_enabled)return `<nav class="asset-pager" data-asset-pager="${esc(name)}" data-per-page="${perPage}" aria-label="分页">${page>1?control('上一页',page-1):''}<span>第 ${page} 页 · 共 ${total} 项</span>${page<last?control('下一页',page+1):''}<input type="hidden" data-asset-page-input max="${last}"></nav>`;
    const nearby=[];for(let p=Math.max(1,page-2);p<=Math.min(last,page+2);p++)nearby.push(control(String(p),p,false,`data-page-distance="${Math.abs(p-page)}" aria-label="第 ${p} 页"${p===page?' aria-current="page"':''}`));
    return `<nav class="asset-pager" data-asset-pager="${esc(name)}" data-per-page="${perPage}" aria-label="分页"><span class="asset-pager-summary">共 ${total} 项 · 第 ${page} / ${last} 页</span><div class="asset-pager-pages">${control('首页',1,page===1)}${control('上一页',page-1,page===1)}${nearby.join('')}${control('下一页',page+1,page===last)}${control('末页',last,page===last)}</div><div class="asset-pager-options"><label>跳至<input type="number" inputmode="numeric" min="1" max="${last}" value="${page}" data-asset-page-input aria-label="跳转页码"></label>${control('跳转','jump')}<label>每页<select data-asset-page-size aria-label="每页条数">${[20,50,100].map(n=>`<option value="${n}"${n===perPage?' selected':''}>${n} 条</option>`).join('')}</select></label></div></nav>`;
  }
  const registerPager=(name,handler)=>pagerHandlers.set(name,handler);
  const pageQuery=(name)=>catalog?.intake_enabled?'&per_page='+pageSizes[name]:'';
  async function changePage(nav,target,size){
    const handler=pagerHandlers.get(nav.dataset.assetPager);if(!handler||pending)return;
    const field=nav.querySelector('[data-asset-page-input]');
    const page=target==='jump'?Number(field.value):Number(target),perPage=size||Number(nav.dataset.perPage);
    if(!Number.isSafeInteger(page)||page<1||page>Number(field.max)){field.setCustomValidity('请输入 1 至 '+field.max+' 的页码');field.reportValidity();field.setCustomValidity('');return;}
    nav.setAttribute('aria-busy','true');
    try{await handler(page,perPage);}catch(error){status(error.message,'error');}
    finally{nav.removeAttribute('aria-busy');}
  }
  registerPager('items',(page,size)=>{listPage=page;pageSizes.items=size;return load();});
  registerPager('loans',(page,size)=>{loanPage=page;pageSizes.loans=size;return load();});
  registerPager('events',(page,size)=>{eventPage=page;pageSizes.events=size;return load();});
  function options(values, selected, empty=''){
    return (empty?`<option value="">${empty}</option>`:'')+values.map(v=>`<option value="${esc(v.id)}"${String(v.id)===String(selected)?' selected':''}>${esc(v.name)}${v.active===0?'（停用）':''}</option>`).join('');
  }
  const supplyName=value=>value==='sufficient'?'充足':'不足';
  function supplyCards(item,campuses,compact=false){
    return (compact?'':'<p class="asset-muted">自由取用，无需扫码或逐次登记；不足时标记，补齐后恢复充足。</p>')+campuses.map(c=>
      `<div class="asset-supply"><span>${esc(c.name)}${c.active?'':'（停用）'} · <strong class="asset-supply-${c.supply_status}">${supplyName(c.supply_status)}</strong></span>`+
      (item.active&&c.active?`<button type="button" class="btn" data-supply-item="${item.id}" data-campus="${c.id}" data-supply-status="${c.supply_status==='sufficient'?'low':'sufficient'}">${c.supply_status==='sufficient'?'标记不足':'补齐了，标记充足'}</button>`:'')+'</div>').join('');
  }
  function itemIcon(item){
    const shape=item.tracking==='status'?'<path d="M4 10h16l1 10H3l1-10ZM7 10V6a5 5 0 0 1 10 0v4M9 15h6"/>':
      item.kind==='returnable'?'<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V4h8v3M3 12h18M9 12v3h6v-3"/>':
      '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4M10 10h4v4h-4z"/>';
    return `<svg class="asset-icon" viewBox="0 0 24 24" aria-hidden="true">${shape}</svg>`;
  }
  function renderListItem(item){
    const supply=item.tracking==='status',equipment=item.kind==='returnable';
    const type=supply?'自由取用':equipment?'可借用品':'数量耗材';
    const hint=supply?'无需逐次登记':item.tracking==='unit'?'按件追踪':item.tracking==='migrating'?'实物核对中':equipment?(item.tracking==='quantity'?'按数量借还':'旧数量台账'):'按量登记';
    const secondary=(item.active&&!['status','unit','migrating'].includes(item.tracking)?button('receive-item','补货入库',item.id)+button('quantity','盘点校正',item.id):'')+
      (catalog.can_manage?button('edit-item','编辑物品',item.id):'')+(window.AssetLabels?button('print-item','打印物料标签',item.id):'');
    return `<article class="asset-card asset-list-row">
      <div class="asset-item-identity">${organizing?`<label class="asset-category-check"><input type="checkbox" data-category-item="${item.id}" ${categorySelection.has(item.id)?'checked':''} aria-label="选择 ${esc(item.name)} M:${item.id}"><span>选择</span></label>`:''}<span class="asset-item-icon">${itemIcon(item)}</span><div class="asset-item-copy">
        <h2><a ${item.tracking==='unit'?'data-unit-model="'+item.id+'"':''} href="/assets/detail?id=${item.id}">${esc(item.name)}</a></h2>
        <p>${esc(item.specification)||'未填写规格'}</p><span class="asset-item-category">${esc(item.category)||'未分类'}</span>
      </div></div>
      <div class="asset-item-mode"><span class="asset-tag${!item.active?' asset-tag-inactive':supply?' asset-tag-supply':equipment?' asset-tag-equipment':''}">${type}${item.active?'':' · 停用'}</span><small>${hint}</small></div>
      <div class="asset-row-stock">${supply?(item.supplies.length?supplyCards(item,item.supplies,true):'<p>尚未配置校区</p>'):
        `<span class="asset-count${!equipment&&!item.available&&item.active?' asset-stock-alert':''}">${equipment?item.total:item.available} <small>${esc(item.unit)}${!equipment&&!item.available&&item.active?' · 缺货':''}</small></span>
        <p>${equipment?`总量 · 在库 ${item.available} / 借出 ${item.borrowed}`:`剩余库存 · 累计领用 ${item.consumed}`}</p>`}</div>
      <div class="asset-actions">${item.active&&!supply?button('register',item.tracking==='unit'?'管理单件':item.tracking==='migrating'?'核对实物':equipment?'登记借用':'登记领用',item.id):''}
        ${secondary?`<details class="asset-row-more"><summary aria-label="${esc(item.name)}的更多操作">更多</summary><div class="asset-more-actions">${secondary}</div></details>`:''}
      </div></article>`;
  }
  async function updateSupply(trigger){
    if(pending)return;
    if(unresolved){recoverPending();return;}
    const item=detail?.item||listing.find(i=>i.id===Number(trigger.dataset.supplyItem));
    const campus=(detail?.campuses||item.supplies).find(c=>c.id===Number(trigger.dataset.campus));
    const saved={actor_id:catalog.member_id,path:'supply-status',request_id:requestId(),body:{item_id:item.id,campus_id:campus.id,
      status:trigger.dataset.supplyStatus,version:campus.stock_version,item_version:item.version}};
    try{rememberPending(saved);}catch(_){status('无法安全保存待确认请求，请检查浏览器会话存储设置后重试。');return;}
    pending=true;showPending();trigger.disabled=true;status('正在更新耗材状态…');
    try{
      await api(saved.path,{...saved.body,request_id:saved.request_id});forgetPending();
      if(await load())status('已标记为'+supplyName(saved.body.status)+'。');
    }catch(error){
      const unknown=!error.status||error.status>=500;if(!unknown)forgetPending();
      status(error.message+(unknown?'；结果尚未确认，请重试上次提交。':'；请刷新数据后核对。'));
    }finally{pending=false;trigger.disabled=false;showPending();}
  }
  function renderLoans(data){
    loans=data.loans;loanPage=data.page;
    return `<h2>${mine?'我的未还借用':'当前借用'}</h2>`+(loans.length?`<div class="asset-grid">${loans.map(l=>`<article class="asset-card">
      <h3><a href="/assets/detail?id=${l.item_id}">${esc(l.name)}</a></h3><p>${esc(l.specification)} · ${esc(l.campus_name)}</p>
      <p>${esc(l.member_name)} · ${esc(l.created_at)}</p><strong>未还 ${l.outstanding} ${esc(l.unit)}</strong>
      <div class="asset-actions">${l.member_id===data.member_id?button('return','归还',l.id):''}</div></article>`).join('')}</div>`:
      '<p class="asset-empty">没有未归还的借用。</p>')+pager('loans',data.page,data.total,data.per_page||pageSizes.loans);
  }
  function renderEvents(data){
    events=data.events;eventPage=data.page;
    return `<h2>操作记录</h2><label>记录校区<select id="asset-event-campus">${options(catalog.campuses,eventCampus,'全部校区')}</select></label><p class="asset-muted">时间为北京时间；纠错追加记录，保留原始流水。</p><ol class="asset-log">${events.map(e=>{
      const movements=(e.details.deltas||[]).map(([id,delta])=>`${esc(catalog.campuses.find(c=>c.id===id)?.name||id)}：在库 ${delta>=0?'+':''}${delta}`).join('；');
      return `<li><strong>#${e.id} ${names[e.action]||esc(e.action)}${!['catalog','supply_status'].includes(e.action)?' · '+e.quantity:''}</strong>
        <p>${esc(e.actor_name)} · 提交：${esc(e.created_at)}${e.member_name?' · 相关成员：'+esc(e.member_name):''}</p>
        ${e.details.occurred_at?`<p>登记时间：${esc(e.details.occurred_at)}（北京时间）</p>`:''}
        <p>${e.action==='supply_status'?supplyName(e.details.before)+' → '+supplyName(e.details.after):movements}</p><p>${esc(e.reason)||'未填写用途'}${e.corrects_id?' · 纠正 #'+e.corrects_id:''}${e.correction_id?' · 已由 #'+e.correction_id+' 纠正':''}</p>
        ${e.receipt?`<details><summary>包装入库来源 · 标签 ${e.receipt.label_quantity??'未知'} / 实收 ${e.receipt.actual_quantity}</summary><pre>${esc(JSON.stringify(e.receipt,null,2))}</pre></details>`:''}
        ${catalog.can_manage&&!e.correction_id&&!e.details.mode_reset&&['receive','adjust','transfer','consume','borrow','return'].includes(e.action)?button('correct','登记纠错',e.id):''}</li>`;
    }).join('')}</ol>${data.total?'':'<p class="asset-empty">暂无记录。</p>'}${pager('events',data.page,data.total,data.per_page||pageSizes.events)}`;
  }
  function updateAssetViews(summary){
    for(const button of document.querySelectorAll('[data-asset-view]')){
      const view=button.dataset.assetView;
      button.setAttribute('aria-pressed',String(view===assetView));
      const count=button.querySelector('.asset-view-count');
      if(count)count.textContent=String(summary?.[view]??0);
    }
  }
  function clearAssetFilters(){
    filter.elements.q.value='';
    filter.elements.campus_id.value='';
    filter.elements.category.value='';
    assetView='all';listPage=1;load();
    filter.elements.q.focus();
  }
  function renderItemDetail(d,l,e){
    const unit=['unit','migrating'].includes(d.item.tracking),supply=d.item.tracking==='status';
    const scanButton=(task,label)=>`<button type="button" class="btn" data-asset-scan-task="${task}" data-item-id="${itemId}">${label}</button>`;
    const scanActions=d.item.active&&!supply&&d.item.tracking!=='migrating'?scanButton('receive','扫码入库')+
      (d.item.kind==='returnable'?scanButton('borrow','扫码借用'):'')+
      (!unit||catalog.can_manage?scanButton('count','扫码盘点'):'')+(catalog.can_manage?scanButton('move','扫码调拨'):''):'';
    return `<div class="asset-detail-overview"><div><span class="asset-tag">${supply?'自由取用':d.item.kind==='returnable'?'设备 / 工具':'数量耗材'}${d.item.active?'':' · 已停用'}</span><p>${esc(d.item.specification)||'未填写规格'} · ${esc(d.item.category)||'未分类'} · 单位：${esc(d.item.unit)}${d.item.kind==='returnable'?' · '+(d.item.tracking==='unit'?'逐件管理':d.item.tracking==='migrating'?'实物核对中':'按数量借还'):''}</p></div><div class="asset-actions">${catalog.can_manage?button('edit-item','编辑资料',itemId):''}${window.AssetLabels?button('print-item','打印标签',itemId):''}${window.AssetUnits&&d.item.kind==='returnable'&&(unit||catalog.can_manage)?button('units',d.item.tracking==='unit'?'管理单件':d.item.tracking==='migrating'?'继续核对实物':'切换为逐件管理',itemId):''}${scanActions}</div></div>
      <nav class="asset-detail-tabs" aria-label="物料详情内容"><button type="button" data-asset-detail-view="stock" aria-pressed="${detailView==='stock'}">库存与借用</button><button type="button" data-asset-detail-view="history" aria-pressed="${detailView==='history'}">操作记录</button></nav>
      <section data-asset-detail-panel="stock" ${detailView==='stock'?'':'hidden'}><h2 class="asset-detail-section-title">各校区库存</h2>
      ${supply?`<div class="asset-detail-supply">${supplyCards(d.item,d.campuses)}</div>`:`<div class="asset-grid">${d.campuses.map(s=>`<article class="asset-card asset-campus-card"><h3>${esc(s.name)}${s.active?'':'（停用）'}</h3><span class="asset-count">${d.item.kind==='returnable'?s.available+s.borrowed:s.available} <small>${esc(d.item.unit)}</small></span><p class="asset-muted">${d.item.kind==='returnable'?`总量 · 在库 ${s.available} / 借出 ${s.borrowed}`:`剩余库存 · 累计领用 ${s.consumed}`}</p><div class="asset-actions">${unit?button('units','查看该校区实物',itemId,`data-campus-id="${s.id}"`):s.active&&d.item.active?button(d.item.kind==='returnable'?'borrow':'consume',d.item.kind==='returnable'?'登记借用':'登记领用',s.id)+button('receive','补货入库',s.id)+`<details class="asset-row-more"><summary>库存维护</summary><div class="asset-more-actions">${button('adjust','盘点校正',s.id)}${catalog.can_manage?button('transfer','校区调拨',s.id):''}</div></details>`:''}</div></article>`).join('')}</div>`}
      ${d.campuses.length?'':'<p class="asset-empty">尚未配置校区，请联系管理人员。</p>'}
      ${!supply&&!unit?`<section class="asset-section">${renderLoans(l)}</section>`:unit?'<p class="asset-muted asset-section">具体设备的借还、保管人和安装位置，可在“管理单件”中查看。</p>':''}</section>
      <section class="asset-detail-history" data-asset-detail-panel="history" ${detailView==='history'?'':'hidden'}>${renderEvents(e)}</section>`;
  }
  async function load(){
    const own=++generation;
    $('asset-content').setAttribute('aria-busy','true');status('正在加载…');
    try{
      const c=await api('catalog'); if(own!==generation)return; catalog=c;serverReceivedAt=Date.now();showPending();
      $('asset-create').hidden=(!c.can_manage&&!window.AssetScan)||!!itemId||mine;
      $('asset-add-manual').hidden=!c.can_manage;
      $('asset-admin-tools').hidden=!c.can_manage||!!itemId||mine;
      $('asset-view-filters').hidden=!!itemId||mine;
      $('asset-result-summary').hidden=!!itemId||mine;
      $('asset-results-heading').hidden=!!itemId||mine;
      $('asset-summary-scope').hidden=!!itemId||mine;
      $('asset-discovery').hidden=!!itemId||mine;
      $('asset-inventory-panel').classList.toggle('asset-secondary-view',!!itemId||mine);
      $('asset-description').textContent=itemId?'查看各校区库存、借用情况与物料流转记录。':mine?'借出的每一件物料，在这里查看和归还。':'从一颗螺丝到一台设备，让每一份物料都有去处。';
      $('asset-back').hidden=!itemId;
      for(const link of document.querySelectorAll('[data-asset-nav]')){
        if((link.dataset.assetNav==='mine')===mine&&!itemId)link.setAttribute('aria-current','page');
        else link.removeAttribute('aria-current');
      }
      $('asset-campuses').hidden=!c.can_manage;
      $('asset-categories').hidden=!c.can_manage;
      if(itemId||mine)organizing=false;
      filter.hidden=!!itemId||mine;
      const campus=filter.elements.campus_id.value;
      filter.elements.campus_id.innerHTML=options(c.campuses,campus,'全部校区');
      if(itemId){
        const [d,l,e]=await Promise.all([api('items/'+itemId),api(`loans?item_id=${itemId}&page=${loanPage}${pageQuery('loans')}`),api(`events?item_id=${itemId}&page=${eventPage}&campus_id=${encodeURIComponent(eventCampus)}${pageQuery('events')}`)]);
        if(own!==generation)return; detail=d;
        $('asset-title').textContent=d.item.name;
        $('asset-content').innerHTML=renderItemDetail(d,l,e);
      }else if(mine){
        const l=await api('loans?mine=1&page='+loanPage+pageQuery('loans'));if(own!==generation)return;
        $('asset-title').textContent='我的借用';
        $('asset-content').innerHTML=(window.AssetScan?'<div class="asset-mine-scan-actions asset-actions"><button type="button" class="btn" data-asset-scan-task="borrow">扫码借用</button><button type="button" class="btn" data-asset-scan-task="return">扫码归还</button></div>':'')+(window.AssetUnits?`<nav class="asset-detail-tabs" aria-label="借用记录类型"><button type="button" data-asset-mine-view="units" aria-pressed="${mineView==='units'}">单件设备与待接收</button><button type="button" data-asset-mine-view="quantity" aria-pressed="${mineView==='quantity'}">按数量借用 · ${l.total} 笔</button></nav>`:'')+`<section id="asset-quantity-loans" ${window.AssetUnits&&mineView!=='quantity'?'hidden':''}>${renderLoans(l)}</section>`;
        if(window.AssetUnits){await window.AssetUnits.loadMine();if(own!==generation)return;$('asset-mine-units').hidden=mineView!=='units';}
      }else{
        const search=new URLSearchParams({q:filter.elements.q.value,campus_id:filter.elements.campus_id.value,category:filter.elements.category.value==='__uncategorized__'?'':filter.elements.category.value,view:assetView,page:String(listPage)});
        if(c.intake_enabled){search.set('per_page',pageSizes.items);if(filter.elements.category.value==='__uncategorized__')search.set('uncategorized','1');}
        const data=await api('items?'+search);if(own!==generation)return;listing=data.items;listPage=data.page;
        const category=filter.elements.category.value;
        filter.elements.category.innerHTML=options([...(c.intake_enabled?[{id:'__uncategorized__',name:'未分类'}]:[]),...data.categories.map(v=>({id:v,name:v}))],category,'全部类别');
        updateAssetViews(data.summary);
        $('asset-result-summary').textContent=`共 ${data.total} 种物料${data.total?' · 第 '+data.page+' 页':''}`;
        const filtered=assetView!=='all'||filter.elements.q.value||filter.elements.campus_id.value||filter.elements.category.value;
        $('asset-content').innerHTML=data.items.length?
          `<div class="asset-list-header" aria-hidden="true"><span>物料信息</span><span>管理方式</span><span>库存概况</span><span>操作</span></div><div class="asset-list">${data.items.map(renderListItem).join('')}</div>${pager('items',data.page,data.total,data.per_page||pageSizes.items)}`:
          `<div class="asset-empty">${itemIcon({kind:'returnable'})}<h3>${filtered?'没有找到符合条件的物料':'物料仓库，准备就绪'}</h3><p>${filtered?'试试其他关键词，或清除筛选查看全部物料。':c.can_manage?'点击右上角“新增 / 扫码入库”，登记第一种物料。':'尚未登记物料，请联系管理人员建档。'}</p>${filtered?'<button class="btn asset-reset" id="asset-clear-empty" type="button">清除筛选</button>':''}</div>${pager('items',data.page,data.total,data.per_page||pageSizes.items)}`;
      }
      updateCategorySelection();
      status('数据已更新。');document.dispatchEvent(new CustomEvent('assets-loaded'));return true;
    }catch(e){if(own===generation)status(e.message+'；可点击“刷新”重试。','error');return false;}
    finally{if(own===generation)$('asset-content').setAttribute('aria-busy','false');}
  }
  const input=(name,label,value='',extra='')=>`<label>${label}<input name="${name}" value="${esc(value)}" ${extra}></label>`;
  const reason=management=>`<label>${management?'原因（必填）':'用途／备注（必填）'}<textarea name="reason" maxlength="500" required rows="3"></textarea></label>`;
  function timeField(){
    const server=Date.parse((catalog.server_time||'').replace(' ','T')+'+08:00');
    const time=new Date((Number.isFinite(server)?server+Date.now()-serverReceivedAt:Date.now())+8*3600000).toISOString().slice(0,16);
    return '<details class="asset-form-section"><summary>补登记时间（默认现在）</summary>'+input('occurred_at','登记时间（北京时间）',time,'type="datetime-local" required')+
      '<p class="asset-muted">只有补录过去的操作时才需要修改。</p></details>';
  }
  function recoverPending(){
    if(!unresolved)return;
    if(pending){status('原请求仍在处理中，请完成后再重试。');return;}
    if(!catalog||unresolved.actor_id!==catalog.member_id){
      status('待确认记录属于其他登录账号，不能使用当前身份重试。');return;
    }
    opener=document.activeElement;operation={recovery:true};
    const body=unresolved.body;
    $('asset-dialog-title').textContent='恢复待确认操作';
    $('asset-fields').innerHTML=
      '<p>只会重发原来的完整请求及相同标识，不会再登记第二笔。</p>'+
      '<p>操作：'+esc(unresolved.path==='supply-status'?'耗材状态 → '+supplyName(body.status):names[body.action]||'目录维护')+' · 数量：'+esc(body.quantity??'—')+'</p>'+
      '<p>用途／原因：'+esc(body.reason||'目录维护')+'</p>';
    $('asset-form-status').textContent='';
    $('asset-save').textContent='重试原请求';
    dialog.classList.remove('asset-dialog-wide');
    $('asset-save').hidden=false;showDialog(dialog);
  }
  function open(title,fields,path,base,numberKeys=[],buildBody=null){
    if(unresolved){recoverPending();return;}
    opener=document.activeElement;operation={path,base,numberKeys,buildBody};
    $('asset-dialog-title').textContent=title;$('asset-fields').innerHTML=fields;$('asset-form-status').textContent='';
    const actionLabel=names[base.action]||{handoff:'移交',accept:'接收',cancel_handoff:'取消移交',move:'移动',install:'安装',detach:'拆卸',fault:'报障',repair:'送修',healthy:'恢复',retire:'报废',lost:'遗失结案',recover:'恢复',correct:'纠正'}[base.action]||'操作';
    $('asset-save').textContent=['operations','units/operate'].includes(path)?'确认'+actionLabel:path.startsWith('catalog/')?'保存资料':'确认保存';
    dialog.classList.toggle('asset-dialog-wide',path==='catalog/items');
    $('asset-save').hidden=false;showDialog(dialog);
  }
  function campusFields(campuses=catalog.campuses,empty='请选择校区'){
    const current=filter.elements.campus_id.value,active=campuses.filter(c=>c.active);
    const selected=active.some(c=>String(c.id)===current)?current:active.length===1?active[0].id:'';
    return `<div class="asset-campus-picker"><label>校区<select name="campus_id" required>${options(active,selected,empty)}</select></label>`+
      (catalog.can_manage?'<label class="asset-inline-choice"><input type="checkbox" data-new-campus> 新建一个校区</label><label data-new-campus-field hidden>新校区名称<input name="new_campus" maxlength="120" disabled required></label>':'')+'</div>';
  }
  async function catalogEditor(kind,value={}){
    if(kind==='items'&&value.id){
      try{value=(await api('items/'+value.id)).item;}
      catch(error){status(error.message,'error');return;}
    }
    const isItem=kind==='items',label={items:'物料',campuses:'校区',categories:'类别'}[kind],base=value.id?{id:value.id,version:value.version}:{};
    let fields=isItem?`<div class="asset-form-context"><strong>${value.id?esc(value.name):'建立物料档案'}</strong><p>${value.id?'这里修改物料资料；补货和盘点从库存清单的“更多”进入。':'先填写基本资料和管理方式，保存后即可登记库存。'}</p></div>`:'';
    fields+='<fieldset class="asset-form-section"><legend>基本信息</legend><div class="asset-form-grid">'+
      input('name','名称',value.name||'','required maxlength="120"')+(isItem?
      input('specification','型号 / 规格',value.specification||'','maxlength="120" placeholder="例如 M3508 / M3×8"')+
      `<label>类别<select name="category">${options((catalog.categories||[]).filter(c=>c.active).map(c=>({id:c.name,name:c.name})),value.category||'','未分类')}</select></label>`+
      input('unit','计量单位',value.unit||'个','required maxlength="12" placeholder="个、台、片…"'):'')+'</div></fieldset>';
    if(isItem)fields+='<fieldset class="asset-form-section"><legend>管理方式</legend><div class="asset-form-grid">'+
      `<label>物料用途<select name="kind">${options([{id:'returnable',name:'设备 / 工具（借用后归还）'},{id:'consumable',name:'耗材（使用后无需归还）'}],value.kind||'returnable')}</select></label>`+
      `<label>可借用品如何管理<select name="returnable_mode">${options([{id:'unit',name:'逐件：每件有独立身份与借还记录'},{id:'quantity',name:'按数量：只记录库存与借还数量'}],value.kind==='returnable'&&['legacy','migrating'].includes(value.tracking)?'':value.tracking==='quantity'&&value.kind==='returnable'?'quantity':'unit',value.kind==='returnable'&&['legacy','migrating'].includes(value.tracking)?'保留当前台账方式':'')}</select></label>`+
      `<label>耗材如何记账<select name="consumable_mode">${options([{id:'quantity',name:'按数量：记录入库与领用'},{id:'status',name:'自由取用：只标记充足 / 不足'}],value.tracking==='status'?'status':'quantity')}</select></label>`+
      '</div><p id="asset-supply-guidance" class="asset-muted"></p></fieldset>';
    if(isItem)fields+='<details class="asset-form-section"><summary>供应商编号'+(value.id?'与启用状态':'（选填）')+'</summary>'+
      (value.supplier_codes?.length?`<p class="asset-muted">已关联：${value.supplier_codes.map(esc).join('、')}</p>`:'')+
      input('supplier_code','关联嘉立创 C 编号','','maxlength="40" placeholder="例如 C12345"');
    if(value.id)fields+=`<label>启用状态<select name="active">${options([{id:'true',name:'启用'},{id:'false',name:'停用，保留历史'}],value.active?'true':'false')}</select></label><p class="asset-muted">有库存或未还借用的物料不能停用；停用类别前需先移出其下的启用物料。</p>`;
    if(isItem)fields+='</details>';
    if(isItem&&!value.id&&catalog.intake_enabled)fields+='<details class="asset-form-section"><summary>与现有档案重名时</summary><label class="asset-inline-choice"><input type="checkbox" name="force_new" value="true"> 确认这是另一物品，另建独立档案</label><label data-force-new-reason hidden>另建原因（必填）<textarea name="reason" maxlength="500" rows="3" disabled required></textarea></label><p class="asset-muted">名称、规格相同的物品通常可共用档案；另建将生成独立 M: 编号。</p></details>';
    if(isItem&&value.id)fields+=reason(true);
    open((value.id?'编辑':'新增')+label,fields,'catalog/'+kind,base);
    if(operation.recovery)return;
    if(isItem){
      if(form.elements.force_new)form.elements.force_new.onchange=()=>{
        const enabled=form.elements.force_new.checked,field=form.querySelector('[data-force-new-reason]');
        field.hidden=!enabled;field.querySelector('textarea').disabled=!enabled;
      };
      const mode=form.elements.consumable_mode,returnableMode=form.elements.returnable_mode,supplier=form.elements.supplier_code;
      const updateMode=()=>{
        const consumable=form.elements.kind.value==='consumable';
        const supplied=!!value.supplier_codes?.length||!!supplier.value.trim();
        mode.disabled=!consumable;mode.closest('label').hidden=!consumable;
        returnableMode.closest('label').hidden=consumable;
        returnableMode.disabled=consumable||value.tracking==='migrating';
        returnableMode.querySelector('option[value=unit]').disabled=value.kind==='returnable'&&value.id&&value.tracking!=='unit';
        returnableMode.querySelector('option[value=quantity]').disabled=value.kind==='returnable'&&value.tracking==='unit';
        mode.querySelector('option[value=status]').disabled=supplied;
        if(!consumable||supplied)mode.value='quantity';
        $('asset-supply-guidance').textContent=!consumable?(value.tracking==='migrating'?'正在核对实物，请先完成或取消核对后再修改管理方式。':returnableMode.value==='unit'?
          '每件设备有独立身份，借还时选择具体设备。是否粘贴自有标签不影响管理方式。':
          returnableMode.value==='quantity'?'按数量记录入库、借用、归还、调拨与盘点，无需为每件建立身份；是否贴标自行决定。'+(value.id?'需要逐件管理时，从详情进入“切换为逐件管理”核对库存与借单。':''):
          '旧库存和未还借单继续保留。选择“按数量”可确认沿用数量台账；需要逐件管理时，从详情核对现有实物。'):mode.value==='status'?
          '适合螺丝等自由取用物料。各校区只需标记充足或不足。':
          value.tracking==='status'?'切换后各校区数量从 0 开始，需要重新盘点。':
          '适合电子元件等数量耗材，入库增加库存，领用扣减库存。';
      };
      mode.onchange=updateMode;
      returnableMode.onchange=updateMode;
      form.elements.kind.onchange=()=>{
        if(!value.id&&form.elements.kind.value==='consumable'&&!supplier.value.trim())mode.value='status';
        updateMode();
      };
      supplier.oninput=updateMode;updateMode();
    }
  }
  function directoryManager(kind){
    const category=kind==='categories',noun=category?'类别':'校区',action=category?'category':'campus';
    $('asset-directory-title').textContent=noun+'管理';$('asset-directory-dialog').dataset.kind=kind;
    $('asset-directory-content').innerHTML='<div class="asset-actions">'+button('new-'+action,'新增'+noun,0)+(category&&catalog.intake_enabled&&!itemId&&!mine?button('category-organize','整理库存类别',0):'')+'</div>'+
      (catalog[kind].length?catalog[kind].map(c=>`<div class="asset-directory-row"><span><strong>${esc(c.name)}</strong><small class="asset-muted">${c.active?'启用中':'已停用'}</small></span>${button('edit-'+action,'编辑',c.id)}</div>`).join(''):'<p class="asset-empty">还没有'+noun+'，点击上方按钮添加。</p>');
    showDialog($('asset-directory-dialog'));
  }
  function updateCategorySelection(){
    const host=$('asset-category-organizer'),toggle=$('asset-bulk-toggle');
    const canAssign=!!catalog?.can_manage&&!!catalog?.intake_enabled;
    if(toggle){
      toggle.hidden=!!itemId||mine||(!window.AssetLabels&&!canAssign);
      toggle.textContent=organizing?'退出多选':`批量操作${categorySelection.size?`（已选 ${categorySelection.size}）`:''}`;
      toggle.setAttribute('aria-pressed',String(organizing));
    }
    if(!host)return;
    host.hidden=!organizing||!!itemId||mine;
    const values=[...categorySelection.values()],outside=values.filter(v=>!listing.some(i=>i.id===v.id)).length;
    $('asset-category-selection').textContent=`已选 ${values.length} / 100 种物料${outside?` · ${outside} 种不在当前页`:''}。翻页、搜索、切换筛选后保留选择；打印只生成所选型号标签，不改变库存。`;
    $('asset-category-selected-list').innerHTML=values.map(v=>`<li><span>${esc(v.name)} · ${esc(v.specification)||'无规格'} · M:${v.id}</span>${button('category-remove','取消选择',v.id,`aria-label="取消选择 M:${v.id}"`)}</li>`).join('');
    const print=host.querySelector('[data-action="bulk-print-labels"]');
    if(print){print.hidden=!window.AssetLabels;print.disabled=!values.length;}
    const assign=host.querySelector('[data-action="category-assign"]');
    if(assign){assign.hidden=!canAssign;assign.disabled=!values.length;}
    const clear=host.querySelector('[data-action="category-clear"]');
    if(clear)clear.disabled=!values.length;
    const page=host.querySelector('[data-action="category-select-page"]');
    if(page)page.disabled=!listing.length;
    for(const checkbox of document.querySelectorAll('[data-category-item]'))checkbox.checked=categorySelection.has(Number(checkbox.dataset.categoryItem));
  }
  function selectCategoryItem(item){
    if(categorySelection.has(item.id))return;
    if(categorySelection.size>=100)throw Error('每批最多选择 100 种物料，请先保存或取消已有选择');
    categorySelection.set(item.id,{...item});
  }
  async function categoryAction(action,id){
    if(action==='category-organize'){
      if(!catalog.can_manage||!catalog.intake_enabled)throw Error('当前账号或服务器暂不支持整理类别');
      organizing=true;$('asset-directory-dialog').close();await load();
      $('asset-category-organizer').scrollIntoView({block:'nearest',behavior:'smooth'});return;
    }
    if(action==='category-organize-close'){organizing=false;return load();}
    if(action==='category-clear'){categorySelection.clear();updateCategorySelection();return;}
    if(action==='category-remove'){categorySelection.delete(id);updateCategorySelection();return;}
    if(action==='category-select-page'){
      if(new Set([...categorySelection.keys(),...listing.map(v=>v.id)]).size>100)throw Error('选择本页后将超过 100 种物料，请先取消部分选择');
      listing.forEach(selectCategoryItem);updateCategorySelection();return;
    }
    if(action==='category-assign'){
      if(!catalog.can_manage||!catalog.intake_enabled)throw Error('当前账号或服务器暂不支持整理类别');
      if(!categorySelection.size)throw Error('请先勾选需要整理的物料');
      const values=[...categorySelection.values()],categories=catalog.categories.filter(c=>c.active);
      open('设置物料类别',`<div class="asset-operation-summary"><strong>本次共 ${values.length} 种物料</strong><p>包括已选清单中不在当前页的物料。同型号的全部实物沿用类别。</p></div><label>目标类别<select name="category_id">${options(categories,'','未分类')}</select></label><p id="asset-category-confirmation" class="asset-operation-summary" aria-live="polite"></p>`,'categories/assign',{},[],data=>{
        const target=categories.find(c=>String(c.id)===data.get('category_id'));
        return {items:values.map(v=>({id:v.id,version:v.version})),category_id:target?.id??null,category_version:target?.version??null};
      });
      if(operation.recovery)return;
      const update=()=>{$('asset-category-confirmation').textContent=`确认将 ${values.length} 种物料设置为“${categories.find(c=>String(c.id)===form.elements.category_id.value)?.name||'未分类'}”。`;};
      form.elements.category_id.onchange=update;update();$('asset-save').textContent='确认修改类别';
    }
  }
  async function act(action,id,campusId){
    if(action==='bulk-toggle'){
      if(itemId||mine)return;
      organizing=!organizing;await load();
      if(organizing)$('asset-category-organizer').scrollIntoView({block:'nearest',behavior:'smooth'});
      return;
    }
    if(action==='bulk-print-labels'){
      if(!window.AssetLabels)throw Error('当前客户端暂不支持标签打印');
      if(!categorySelection.size)throw Error('请先勾选需要打印的物料');
      return window.AssetLabels.open([...categorySelection.values()]);
    }
    if(action.startsWith('category-'))return categoryAction(action,id);
    if(action==='new-campus'||action==='edit-campus'){catalogEditor('campuses',catalog.campuses.find(c=>c.id===id)||{});return;}
    if(action==='new-category'||action==='edit-category'){catalogEditor('categories',catalog.categories.find(c=>c.id===id)||{});return;}
    if(action==='units'){
      if(campusId)$('asset-unit-campus').innerHTML=options(catalog.campuses,campusId,'全部校区');
      await window.AssetUnits.show(id);return;
    }
    if(action==='print-item'){window.AssetLabels.open([detail?.item||listing.find(i=>i.id===id)]);return;}
    if(action==='edit-item'){catalogEditor('items',detail?.item||listing.find(i=>i.id===id));return;}
    if((action==='register'||action==='quantity')&&['unit','migrating'].includes(listing.find(i=>i.id===id)?.tracking)){await window.AssetUnits.show(id);return;}
    if(action==='register'||action==='quantity'||action==='receive-item'){
      pending=true;const trigger=document.activeElement;status('正在读取校区库存…');
      try{
        const data=await api('items/'+id);
        const eligible=data.campuses.filter(c=>c.active);
        if(!data.item.active||!eligible.length)throw Error('该物品已停用或没有可用校区');
        const selected=eligible.find(c=>String(c.id)===filter.elements.campus_id.value)?.id||(eligible.length===1?eligible[0].id:'');
        const type=action==='quantity'?'adjust':action==='receive-item'?'receive':data.item.kind==='returnable'?'borrow':'consume';
        open(action==='quantity'?'盘点校正':names[type]+'登记',
          `<div class="asset-form-context"><strong>${esc(data.item.name)}</strong><p>${esc(data.item.specification)} · 单位：${esc(data.item.unit)}</p></div>`+
          `<label>校区<select name="campus_id" required>${options(eligible,selected,'请选择校区')}</select></label><p id="asset-campus-balance" role="status"></p>`+
          input('quantity',type==='adjust'?'实物盘点后的在库数量':type==='receive'?'本次入库数量':type==='borrow'?'本次借用数量':'本次领用数量',type==='adjust'?'':1,`type="number" inputmode="numeric" min="${type==='adjust'?0:1}" max="1000000000" step="1" required`)+
          '<p id="asset-quantity-preview" class="asset-operation-summary" aria-live="polite"></p>'+
          timeField()+reason(type==='adjust'),'operations',{action:type,item_id:id},['quantity','campus_id']);
        if(operation.recovery)return;
        opener=trigger;operation.campuses=eligible;updateCampusBalance();status('请选择校区并完成登记。');
      }catch(error){status(error.message);}finally{pending=false;}
      return;
    }
    if(action.includes('-prev')||action.includes('-next')){
      if(action.startsWith('items'))listPage=id;
      if(action.startsWith('loans'))loanPage=id;
      if(action.startsWith('events'))eventPage=id;
      load();return;
    }
    if(action==='correct'){
      const record=events.find(e=>e.id===id);
      open('登记纠错',`<p>撤销 #${id} 的数量影响，保留原记录。已有后续操作时可能无法撤销，请核对后再提交。</p>${reason(true)}`,'operations',{action,event_id:record.id});return;
    }
    if(action==='return'){
      const l=loans.find(v=>v.id===id);
      open('归还',`<p>${esc(l.name)} · 归还至 ${esc(l.campus_name)} · 未还 ${l.outstanding} ${esc(l.unit)}</p>`+
        input('quantity','本次归还数量',l.outstanding,`type="number" inputmode="numeric" min="1" max="${l.outstanding}" step="1" required`)+timeField()+reason(false),
        'operations',{action,loan_id:id},['quantity']);return;
    }
    const campus=detail.campuses.find(c=>c.id===id),base={action,item_id:itemId,campus_id:id};
    if(action==='adjust')base.version=campus.stock_version;
    let fields=`<div class="asset-form-context"><strong>${esc(detail.item.name)}</strong><p>${esc(campus.name)} · 在库 ${campus.available} ${esc(detail.item.unit)}</p></div><p id="asset-campus-balance" role="status"></p>`+
      input('quantity',action==='adjust'?'盘点后的在库数量':'数量',action==='adjust'?campus.available:1,
        `type="number" inputmode="numeric" min="${action==='adjust'?0:1}" max="1000000000" step="1" required`);
    if(action==='transfer')fields+=`<label>调入校区<select name="target_campus_id" required>${options(catalog.campuses.filter(c=>c.active&&c.id!==id),'','请选择调入校区')}</select></label>`;
    fields+='<p id="asset-quantity-preview" class="asset-operation-summary" aria-live="polite"></p>'+timeField()+reason(['receive','adjust','transfer'].includes(action));
    open(names[action],fields,'operations',base,['quantity','target_campus_id']);
    if(!operation.recovery){operation.campuses=[campus];updateCampusBalance();}
  }
  async function scanQuantity(task,id,context={}){
    if(unresolved){recoverPending();return;}
    if(pending)throw Error('当前操作正在处理，请稍后再试');
    const action={count:'adjust',move:'transfer',receive:'receive',borrow:'borrow',return:'return'}[task];
    if(!action)throw Error('请选择有效的库存操作');
    const data=await api('items/'+id),item=data.item;
    if(['unit','migrating','status'].includes(item.tracking))throw Error('此物料不使用数量操作，请选择对应的实物或管理方式');
    if(!item.active&&action!=='return')throw Error('此物料已停用');
    if(['borrow','return'].includes(action)&&item.kind!=='returnable')throw Error('耗材无需办理借还');
    if(action==='transfer'&&!catalog.can_manage)throw Error('校区调拨需由物料管理员办理');
    const summary=`<div class="asset-form-context"><strong>${esc(item.name)}</strong><p>${esc(item.specification)} · 按数量管理 · 单位：${esc(item.unit)}</p></div>`;
    if(action==='return'){
      let matches=[],page=1,total=0;
      do{
        const result=await api(`loans?mine=1&item_id=${id}&page=${page++}`);
        matches.push(...result.loans);total=result.total;
        if(!result.loans.length)break;
      }while(matches.length<total);
      if(!matches.length)throw Error('您没有此物料的未还借单');
      const loanOptions=matches.map(l=>({id:l.id,name:`${l.campus_name} · 未还 ${l.outstanding} ${l.unit} · 借单 #${l.id}`}));
      open('扫码归还 · '+item.name,summary+`<label>归还哪笔借用<select name="loan_id" required>${options(loanOptions,matches.length===1?matches[0].id:'','请选择借单')}</select></label>`+
        input('quantity','本次归还数量',matches.length===1?matches[0].outstanding:'','type="number" min="1" step="1" required')+timeField()+reason(false),
        'operations',{action},['loan_id','quantity']);
      if(operation.recovery)return;
      const update=()=>{const loan=matches.find(l=>l.id===Number(form.elements.loan_id.value));form.elements.quantity.max=loan?.outstanding||1;form.elements.quantity.value=loan?.outstanding||'';};
      form.elements.loan_id.onchange=update;update();return;
    }
    const campuses=data.campuses.filter(c=>c.active);
    if(!campuses.length)throw Error('没有可用校区，请先创建校区');
    const wanted=context.campusId||filter.elements.campus_id.value;
    const chosen=campuses.find(c=>String(c.id)===String(wanted))?.id||(campuses.length===1?campuses[0].id:'');
    let fields=summary+`<label>${action==='transfer'?'调出校区':'实际校区'}<select name="campus_id" required>${options(campuses,chosen,'请选择校区')}</select></label><p id="asset-campus-balance" role="status"></p>`+
      input('quantity',action==='adjust'?'盘点后的在库数量':action==='receive'?'本次实收数量':'本次数量',action==='adjust'?'':1,`type="number" inputmode="numeric" min="${action==='adjust'?0:1}" max="1000000000" step="1" required`);
    if(action==='transfer')fields+=`<label>调入校区<select name="target_campus_id" required>${options(campuses,'','请选择调入校区')}</select></label>`;
    fields+='<p id="asset-quantity-preview" class="asset-operation-summary" aria-live="polite"></p>'+timeField()+reason(action!=='borrow');
    open('扫码'+names[action]+' · '+item.name,fields,'operations',{action,item_id:id},['campus_id','quantity','target_campus_id']);
    if(!operation.recovery){
      operation.campuses=campuses;updateCampusBalance();
      if(action==='transfer'){
        const updateTarget=()=>{const target=form.elements.target_campus_id,source=form.elements.campus_id.value,current=target.value;target.innerHTML=options(campuses.filter(c=>String(c.id)!==source),current===source?'':current,'请选择调入校区');};
        form.elements.campus_id.addEventListener('change',updateTarget);updateTarget();
      }
    }
  }
  function updateCampusBalance(){
    if(!operation?.campuses)return;
    const campus=operation.campuses.find(c=>String(c.id)===String(form.elements.campus_id?.value||operation.base.campus_id));
    $('asset-campus-balance').textContent=campus?`在库 ${campus.available} · 借出未还 ${campus.borrowed}`:'请先选择实际操作校区。';
    if(operation.base.action==='adjust')form.elements.quantity.value=campus?campus.available:'';
    if(['borrow','consume','transfer'].includes(operation.base.action))form.elements.quantity.max=campus?campus.available:1000000000;
    updateQuantityPreview();
  }
  function updateQuantityPreview(){
    const preview=$('asset-quantity-preview');if(!preview||!operation?.campuses)return;
    const campus=operation.campuses.find(c=>String(c.id)===String(form.elements.campus_id?.value||operation.base.campus_id));
    if(!campus){preview.textContent='选择校区后显示库存变化。';return;}
    const field=form.elements.quantity,quantity=Number(field.value),action=operation.base.action;
    if(field.value===''||!Number.isSafeInteger(quantity)||quantity<Number(field.min)){preview.textContent='请填写有效数量。';return;}
    const after=action==='adjust'?quantity:campus.available+(action==='receive'?quantity:-quantity);
    preview.textContent=after<0?'当前库存不足，请减少数量或选择其他校区。':`当前在库 ${campus.available} → 提交后 ${after}（${after>=campus.available?'增加':'减少'} ${Math.abs(after-campus.available)}）`;
  }
  form.addEventListener('input',e=>{if(e.target.name==='quantity')updateQuantityPreview();});
  form.addEventListener('change',e=>{
    if(e.target.name==='campus_id')updateCampusBalance();
    if(e.target.matches('[data-new-campus]')){
      const picker=e.target.closest('.asset-campus-picker'),creating=e.target.checked;
      picker.querySelector('[name=campus_id]').disabled=creating;
      picker.querySelector('[data-new-campus-field]').hidden=!creating;
      picker.querySelector('[name=new_campus]').disabled=!creating;
      picker.querySelector(creating?'[name=new_campus]':'[name=campus_id]').focus();
    }
  });
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(pending||!operation)return;
    const recovery=!!operation.recovery;
    let body=recovery?unresolved?.body:{...operation.base};
    if(!body)return;
    if(!recovery){
      for(const [key,value] of new FormData(form)){
        if(key==='returnable_mode'&&value==='')continue;
        body[key]=operation.numberKeys.includes(key)?Number(value):['active','detached','destroyed','force_new'].includes(key)?value==='true':key==='serials'?(value.trim()?value.split('\n').map(s=>s.trim()):undefined):value;
      }
      if(operation.buildBody)body=operation.buildBody(new FormData(form));
      if(operation.campuses&&body.action==='adjust')body.version=operation.campuses.find(c=>c.id===body.campus_id)?.stock_version;
    }
    if(unresolved&&(unresolved.actor_id!==catalog?.member_id||
        unresolved.path!==(recovery?unresolved.path:operation.path)||
        JSON.stringify(unresolved.body)!==JSON.stringify(body))){
      $('asset-form-status').textContent='上一笔操作尚未确认，不能修改内容或新增登记；关闭弹窗后选择“重试原请求”。';
      return;
    }
    let saved=unresolved;
    if(!saved){
      saved={actor_id:catalog.member_id,path:operation.path,body,request_id:requestId()};
      if(new TextEncoder().encode(JSON.stringify({...body,request_id:saved.request_id})).length>16384){
        $('asset-form-status').textContent='本次资料超过请求大小限制，请减少条目或缩短内容后重新提交。当前资料已保留。';return;
      }
      try{rememberPending(saved);}
      catch(_){
        $('asset-form-status').textContent='无法安全保存待确认请求，请检查浏览器会话存储设置后重试。';
        return;
      }
    }
    pending=true;showPending();$('asset-save').disabled=true;$('asset-close').disabled=true;$('asset-form-cancel').disabled=true;$('asset-form-status').textContent='正在保存…';
    try{
      await api(saved.path,{...saved.body,request_id:saved.request_id});
      forgetPending();
      document.dispatchEvent(new CustomEvent("asset-operation-saved",{detail:saved}));
      dialog.close();const refreshed=await load();if(refreshed)status('已保存。');
    }catch(error){
      const unknown=!error.status||error.status>=500;
      if(!unknown)forgetPending();
      $('asset-form-status').textContent=error.message+(unknown?'；结果尚未确认，可重试原请求；刷新或重新打开页面后仍会保留该请求。':'');
      $('asset-form-status').focus();
    }finally{pending=false;showPending();$('asset-save').disabled=false;$('asset-close').disabled=false;$('asset-form-cancel').disabled=false;}
  });
  dialog.addEventListener('cancel',e=>{if(pending)e.preventDefault();});
  $('asset-close').onclick=()=>{if(!pending)dialog.close();};
  $('asset-form-cancel').onclick=()=>$('asset-close').click();
  $('asset-directory-close').onclick=()=>$('asset-directory-dialog').close();
  document.addEventListener('assets-loaded',()=>{if($('asset-directory-dialog').open)directoryManager($('asset-directory-dialog').dataset.kind);});
  document.addEventListener('click',e=>{
    for(const menu of document.querySelectorAll('.asset-row-more[open]')){
      if(!menu.contains(e.target))menu.open=false;
    }
    if(pending)return;
    const supply=e.target.closest('[data-supply-item]');
    if(supply){updateSupply(supply);return;}
    const b=e.target.closest('[data-action]');
    if(b){
      const menu=b.closest('.asset-row-more');
      // Keep a visible focus target for the dialog's existing return-focus logic.
      if(menu){menu.open=false;menu.querySelector('summary').focus();}
      act(b.dataset.action,Number(b.dataset.id),b.dataset.campusId).catch(error=>status(error.message,'error'));
    }
    else if(!e.target.closest('a,button,input,select,textarea')){
      const card=e.target.closest('[data-register]');
      if(card){card.querySelector('[data-action=register]').focus();act('register',Number(card.dataset.register));}
    }
  });
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape'||document.querySelector('dialog[open]'))return;
    const menu=document.activeElement?.closest('.asset-row-more[open]');
    if(menu){menu.open=false;menu.querySelector('summary').focus();}
  });
  document.addEventListener('change',e=>{if(e.target.id==='asset-event-campus'){eventCampus=e.target.value;eventPage=1;load();}});
  document.addEventListener('click',e=>{
    const control=e.target.closest('[data-asset-page]');if(control)changePage(control.closest('[data-asset-pager]'),control.dataset.assetPage);
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&e.target.matches('[data-asset-page-input]')){e.preventDefault();changePage(e.target.closest('[data-asset-pager]'),'jump');}
  });
  document.addEventListener('change',e=>{
    if(e.target.matches('[data-asset-page-size]'))changePage(e.target.closest('[data-asset-pager]'),1,Number(e.target.value));
    if(e.target.matches('[data-category-item]')){
      const id=Number(e.target.dataset.categoryItem);
      try{if(e.target.checked)selectCategoryItem(listing.find(v=>v.id===id));else categorySelection.delete(id);}
      catch(error){status(error.message,'error');}updateCategorySelection();
    }
  });
  document.addEventListener('asset-operation-saved',e=>{
    if(e.detail.path==='categories/assign'){
      for(const v of e.detail.body.items)if(categorySelection.get(v.id)?.version===v.version)categorySelection.delete(v.id);
      updateCategorySelection();
    }
  });
  $('asset-create').onclick=()=>{
    if(unresolved){recoverPending();return;}
    $('asset-add-guidance').textContent=catalog.can_manage?'可手动新建目录，或扫描标签匹配已有物品并核对入库。':'可扫码登记已有耗材；新物品建档需由管理人员确认。';
    showDialog($('asset-add-dialog'));
  };
  $('asset-add-close').onclick=()=>$('asset-add-dialog').close();
  $('asset-add-manual').onclick=()=>catalogEditor('items');
  $('asset-campuses').onclick=()=>directoryManager('campuses');
  $('asset-categories').onclick=()=>directoryManager('categories');
  $('asset-pending-retry').onclick=()=>recoverPending();
  $('asset-refresh').onclick=()=>load();
  let assetSearchTimer=null;
  filter.onsubmit=e=>{e.preventDefault();clearTimeout(assetSearchTimer);listPage=1;load();};
  filter.elements.q.addEventListener('input',()=>{
    clearTimeout(assetSearchTimer);
    assetSearchTimer=setTimeout(()=>{listPage=1;load();},350);
  });
  $('asset-filter-reset').onclick=clearAssetFilters;
  document.addEventListener('click',event=>{
    const mineButton=event.target.closest('[data-asset-mine-view]');
    if(mineButton){
      mineView=mineButton.dataset.assetMineView;
      for(const control of document.querySelectorAll('[data-asset-mine-view]'))control.setAttribute('aria-pressed',String(control.dataset.assetMineView===mineView));
      $('asset-quantity-loans').hidden=mineView!=='quantity';$('asset-mine-units').hidden=mineView!=='units';return;
    }
    const detailButton=event.target.closest('[data-asset-detail-view]');
    if(detailButton){
      detailView=detailButton.dataset.assetDetailView;
      for(const control of document.querySelectorAll('[data-asset-detail-view]'))control.setAttribute('aria-pressed',String(control.dataset.assetDetailView===detailView));
      for(const panel of document.querySelectorAll('[data-asset-detail-panel]'))panel.hidden=panel.dataset.assetDetailPanel!==detailView;
      return;
    }
    const viewButton=event.target.closest('[data-asset-view]');
    if(viewButton){
      assetView=viewButton.dataset.assetView;listPage=1;load();
    }else if(event.target.closest('#asset-clear-empty'))clearAssetFilters();
  });
  filter.addEventListener('change',e=>{
    if(e.target.name==='campus_id'||e.target.name==='category'){listPage=1;load();}
  });
  window.AssetLedger={api,load,open,showDialog,input,reason,options,esc,campusFields,scanQuantity,renderPager:pager,registerPager,get catalog(){return catalog;},get blocked(){return !!unresolved||pending;},get detail(){return detail;},get listing(){return listing;}};
  window.addEventListener('load',updateCategorySelection,{once:true});
  load();
})();
