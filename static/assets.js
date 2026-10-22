/* One backend ledger for browser and packaged client pages. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), esc=window.BusinessUI.escape;
  const params=new URLSearchParams(location.search), itemId=Number(params.get('id'))||null;
  const mine=params.get('view')==='mine';
  const names={receive:'入库',borrow:'借用',return:'归还',consume:'领用',adjust:'盘点调整',transfer:'校区调拨',correct:'纠错',catalog:'目录维护'};
  let catalog, detail, listing=[], loans=[], events=[], generation=0, listPage=1, loanPage=1, eventPage=1, eventCampus='';
  let operation=null, pending=false, opener=null, serverReceivedAt=0;
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
  // Keep scrolling inside the rounded shell; only a complete backdrop click dismisses.
  for(const modal of document.querySelectorAll('dialog.asset-dialog')){
    const scroll=document.createElement('div');scroll.className='asset-dialog-scroll';
    scroll.append(...modal.childNodes);modal.append(scroll);
    const outside=e=>{const r=modal.getBoundingClientRect();return e.target===modal&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
    let beganOutside=false;
    modal.addEventListener('pointerdown',e=>{beganOutside=outside(e);});
    modal.addEventListener('click',e=>{
      if(beganOutside&&outside(e)&&!pending)modal.querySelector('header button')?.click();
      beganOutside=false;
    });
  }
  const requestId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
  const status=(message)=>{$('asset-status').textContent=message;};
  async function api(path, body){
    const response=await fetch('/api/assets/'+path, body?{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    }:{});
    let data;
    try{data=await response.json();}catch(_){throw new Error('服务器返回了无法识别的内容');}
    if(!response.ok||data?.ok!==true){
      const error=new Error(typeof data?.error==='string'?data.error:'服务器返回了无法识别的内容');
      // Only a structured business rejection proves the write did not succeed.
      error.businessRejected=response.status>=400&&response.status<500&&data?.ok===false&&typeof data.error==='string';
      throw error;
    }
    return data;
  }
  const button=(action,label,id)=>`<button type="button" class="btn" data-action="${action}" data-id="${id}">${label}</button>`;
  function pager(name,page,total){
    return `<nav class="asset-pager" aria-label="分页">${page>1?button(name+'-prev','上一页',page-1):''}<span>第 ${page} 页 · 共 ${total} 项</span>${page*20<total?button(name+'-next','下一页',page+1):''}</nav>`;
  }
  function options(values, selected, empty=''){
    return (empty?`<option value="">${empty}</option>`:'')+values.map(v=>`<option value="${esc(v.id)}"${String(v.id)===String(selected)?' selected':''}>${esc(v.name)}${v.active===0?'（停用）':''}</option>`).join('');
  }
  function renderLoans(data){
    loans=data.loans;
    return `<h2>${mine?'我的未还借用':'当前借用'}</h2>`+(loans.length?`<div class="asset-grid">${loans.map(l=>`<article class="asset-card">
      <h3><a href="/assets/detail?id=${l.item_id}">${esc(l.name)}</a></h3><p>${esc(l.specification)} · ${esc(l.campus_name)}</p>
      <p>${esc(l.member_name)} · ${esc(l.created_at)}</p><strong>未还 ${l.outstanding} ${esc(l.unit)}</strong>
      <div class="asset-actions">${l.member_id===data.member_id?button('return','归还',l.id):''}</div></article>`).join('')}</div>`:
      '<p class="asset-empty">没有未归还的借用。</p>')+pager('loans',data.page,data.total);
  }
  function renderEvents(data){
    events=data.events;
    return `<h2>操作记录</h2><label>记录校区<select id="asset-event-campus">${options(catalog.campuses,eventCampus,'全部校区')}</select></label><p class="asset-muted">时间为北京时间；纠错追加记录，保留原始流水。</p><ol class="asset-log">${events.map(e=>{
      const movements=(e.details.deltas||[]).map(([id,delta])=>`${esc(catalog.campuses.find(c=>c.id===id)?.name||id)}：在库 ${delta>=0?'+':''}${delta}`).join('；');
      return `<li><strong>#${e.id} ${names[e.action]||esc(e.action)}${e.action!=='catalog'?' · '+e.quantity:''}</strong>
        <p>${esc(e.actor_name)} · 提交：${esc(e.created_at)}${e.member_name?' · 相关成员：'+esc(e.member_name):''}</p>
        ${e.details.occurred_at?`<p>登记时间：${esc(e.details.occurred_at)}（北京时间）</p>`:''}
        <p>${movements}</p><p>${esc(e.reason)||'未填写用途'}${e.corrects_id?' · 纠正 #'+e.corrects_id:''}${e.correction_id?' · 已由 #'+e.correction_id+' 纠正':''}</p>
        ${e.receipt?`<details><summary>包装入库来源 · 标签 ${e.receipt.label_quantity??'未知'} / 实收 ${e.receipt.actual_quantity}</summary><pre>${esc(JSON.stringify(e.receipt,null,2))}</pre></details>`:''}
        ${catalog.can_manage&&!e.correction_id&&['receive','adjust','transfer','consume','borrow','return'].includes(e.action)?button('correct','登记纠错',e.id):''}</li>`;
    }).join('')}</ol>${data.total?'':'<p class="asset-empty">暂无记录。</p>'}${pager('events',data.page,data.total)}`;
  }
  async function load(){
    const own=++generation;
    $('asset-content').setAttribute('aria-busy','true');status('正在加载…');
    try{
      const c=await api('catalog'); if(own!==generation)return; catalog=c;serverReceivedAt=Date.now();showPending();
      $('asset-create').hidden=(!c.can_manage&&!window.AssetScan)||!!itemId||mine;
      $('asset-add-manual').hidden=!c.can_manage;
      $('asset-campuses').hidden=!c.can_manage;
      $('asset-categories').hidden=!c.can_manage;
      filter.hidden=!!itemId||mine;
      const campus=filter.elements.campus_id.value;
      filter.elements.campus_id.innerHTML=options(c.campuses,campus,'全部校区');
      if(itemId){
        const [d,l,e]=await Promise.all([api('items/'+itemId),api(`loans?item_id=${itemId}&page=${loanPage}`),api(`events?item_id=${itemId}&page=${eventPage}&campus_id=${encodeURIComponent(eventCampus)}`)]);
        if(own!==generation)return; detail=d;
        $('asset-title').textContent=d.item.name;
        $('asset-content').innerHTML=`<p>${esc(d.item.specification)} · ${esc(d.item.category||'未分类')} · ${d.item.kind==='returnable'?'可借用品':'耗材'}${d.item.active?'':' · 已停用'}</p>
          ${c.can_manage?button('edit-item','编辑物品',itemId):''}${window.AssetLabels?button('print-item','打印物料标签',itemId):''}${window.AssetUnits&&d.item.kind==='returnable'?button('units',d.item.tracking==='unit'?'管理单件':'实物核对迁移',itemId):''}
          <div class="asset-grid asset-section">${d.campuses.map(s=>`<article class="asset-card"><h2>${esc(s.name)}${s.active?'':'（停用）'}</h2>
          <span class="asset-count">${d.item.kind==='returnable'?s.available+s.borrowed:s.available} <small>${esc(d.item.unit)}</small></span>
          <p>${d.item.kind==='returnable'?`总量 · 在库 ${s.available} / 借出 ${s.borrowed}`:`剩余库存 · 累计领用 ${s.consumed}`}</p>
          <div class="asset-actions">${['unit','migrating'].includes(d.item.tracking)?button('units','管理单件',itemId):s.active&&d.item.active?
            button(d.item.kind==='returnable'?'borrow':'consume',d.item.kind==='returnable'?'借用':'领用',s.id)+
            ['receive','adjust'].map(a=>button(a,names[a],s.id)).join('')+
            (c.can_manage?button('transfer',names.transfer,s.id):''):''}</div></article>`).join('')}</div>
          ${d.campuses.length?'':'<p class="asset-empty">尚未配置校区，请联系资产管理员。</p>'}
          <section class="asset-section">${renderLoans(l)}</section><section class="asset-section">${renderEvents(e)}</section>`;
      }else if(mine){
        const l=await api('loans?mine=1&page='+loanPage);if(own!==generation)return;
        $('asset-title').textContent='我的借用';$('asset-content').innerHTML=renderLoans(l);
        if(window.AssetUnits){await window.AssetUnits.loadMine();if(own!==generation)return;}
      }else{
        const search=new URLSearchParams({q:filter.elements.q.value,campus_id:filter.elements.campus_id.value,category:filter.elements.category.value,page:String(listPage)});
        const data=await api('items?'+search);if(own!==generation)return;listing=data.items;
        const category=filter.elements.category.value;
        filter.elements.category.innerHTML=options(data.categories.map(v=>({id:v,name:v})),category,'全部类别');
        $('asset-content').innerHTML=data.items.length?`<div class="asset-grid">${data.items.map(i=>`<article class="asset-card"${i.active?` data-register="${i.id}"`:''}>
          <span class="asset-tag">${i.kind==='returnable'?'可借用品':'耗材'}${i.active?'':' · 停用'}</span>
          <h2><a ${i.tracking==='unit'?'data-unit-model="'+i.id+'"':''} href="/assets/detail?id=${i.id}">${esc(i.name)}</a></h2><p>${esc(i.specification)||'未填写规格'} · ${esc(i.category)||'未分类'}</p>
          <span class="asset-count">${i.kind==='returnable'?i.total:i.available} <small>${esc(i.unit)}</small></span>
          <p>${i.kind==='returnable'?`总量 · 在库 ${i.available} / 借出 ${i.borrowed}`:`剩余库存 · 累计领用 ${i.consumed}`}</p>
          <div class="asset-actions">${i.active?button('register',i.tracking==='unit'?'管理单件':i.kind==='returnable'?'登记借用':'登记领用',i.id)+(i.tracking==='unit'?'':button('quantity','调整数量',i.id)):''}
          ${c.can_manage?button('edit-item','编辑物品',i.id):''}${window.AssetLabels?button('print-item','打印物料标签',i.id):''}</div></article>`).join('')}</div>${pager('items',data.page,data.total)}`:
          '<p class="asset-empty">暂无符合条件的物品。管理员可先配置校区，再新增物品并登记入库。</p>';
      }
      status('数据已更新。');document.dispatchEvent(new CustomEvent('assets-loaded'));return true;
    }catch(e){if(own===generation)status(e.message+'；可点击“刷新数据”重试。');return false;}
    finally{if(own===generation)$('asset-content').setAttribute('aria-busy','false');}
  }
  const input=(name,label,value='',extra='')=>`<label>${label}<input name="${name}" value="${esc(value)}" ${extra}></label>`;
  const reason=management=>`<label>${management?'原因（必填）':'用途／备注（必填）'}<textarea name="reason" maxlength="500" required rows="3"></textarea></label>`;
  function timeField(){
    const server=Date.parse((catalog.server_time||'').replace(' ','T')+'+08:00');
    const time=new Date((Number.isFinite(server)?server+Date.now()-serverReceivedAt:Date.now())+8*3600000).toISOString().slice(0,16);
    return input('occurred_at','登记时间（北京时间）',time,'type="datetime-local" required')+
      '<p class="asset-muted">可补登记实际发生时间；提交时间由服务器另行记录。</p>';
  }
  function recoverPending(){
    if(!unresolved)return;
    if(!catalog||unresolved.actor_id!==catalog.member_id){
      status('待确认记录属于其他登录账号，不能使用当前身份重试。');return;
    }
    opener=document.activeElement;operation={recovery:true};
    const body=unresolved.body;
    $('asset-dialog-title').textContent='恢复待确认操作';
    $('asset-fields').innerHTML=
      '<p>只会重发原来的完整请求及相同标识，不会再登记第二笔。</p>'+
      '<p>操作：'+esc(names[body.action]||'目录维护')+' · 数量：'+esc(body.quantity??'—')+'</p>'+
      '<p>用途／原因：'+esc(body.reason||'目录维护')+'</p>';
    $('asset-form-status').textContent='';
    $('asset-save').textContent='重试原请求';
    $('asset-save').hidden=false;dialog.showModal();
  }
  function open(title,fields,path,base,numberKeys=[],buildBody=null){
    if(unresolved){recoverPending();return;}
    opener=document.activeElement;operation={path,base,numberKeys,buildBody};
    $('asset-dialog-title').textContent=title;$('asset-fields').innerHTML=fields;$('asset-form-status').textContent='';
    $('asset-save').textContent='确认保存';
    $('asset-save').hidden=false;dialog.showModal();
  }
  function campusFields(campuses=catalog.campuses,empty='请选择校区'){
    return `<label>校区<select name="campus_id">${options(campuses.filter(c=>c.active),'',empty)}</select></label>`+
      (catalog.can_manage?input('new_campus','或新增校区（填写后请将上方校区留空）','','maxlength="120"'):'');
  }
  async function catalogEditor(kind,value={}){
    let campuses=[];
    if(kind==='items'&&value.id){
      try{const fresh=await api('items/'+value.id);value=fresh.item;campuses=fresh.campuses;}
      catch(error){status(error.message);return;}
    }
    const isItem=kind==='items',label={items:'物品',campuses:'校区',categories:'类别'}[kind],base=value.id?{id:value.id,version:value.version}:{};
    let fields=input('name','名称',value.name||'','required maxlength="120"')+(isItem?
      input('specification','型号／规格',value.specification||'','maxlength="120"')+input('supplier_code','新增嘉立创 C 编号（选填）','','maxlength="40"')+
      `<label>类别<select name="category">${options((catalog.categories||[]).filter(c=>c.active).map(c=>({id:c.name,name:c.name})),value.category||'','未分类')}</select></label>`+
      input('unit','计量单位',value.unit||'个','required maxlength="12"')+
      `<label>物品类型<select name="kind">${options([{id:'returnable',name:'可借用品（需归还）'},{id:'consumable',name:'耗材（领用消耗）'}],value.kind||'returnable')}</select></label>`:'')+
      (value.id?`<label>启用状态<select name="active">${options([{id:'true',name:'启用'},{id:'false',name:'停用（移出使用目录，保留历史）'}],value.active?'true':'false')}</select></label><p class="asset-muted">物品有库存或未还借单时不可停用；类别下有启用物品时，请先更换其类别。</p>`:'');
    const quantityEditor=isItem&&value.id&&!['unit','migrating'].includes(value.tracking);
    if(quantityEditor)fields+='<fieldset><legend>校区与数量</legend><p>选择校区可修改该校区在库数量，其他校区和未还借单保持原值。留空则只修改物品资料。</p>'+
      campusFields(campuses,'不调整库存')+'<p id="asset-campus-balance" role="status"></p>'+input('quantity','调整后的在库数量','','type="number" min="0" step="1"')+'</fieldset>';
    if(isItem&&value.id)fields+=reason(true);
    if(isItem&&['unit','migrating'].includes(value.tracking))fields+='<p>数量按具体设备统计。增减数量、调整校区，请选择具体实物操作。</p><button type="button" class="btn" id="asset-edit-units">调整单件数量与校区</button>';
    open((value.id?'编辑':'新增')+label,fields,'catalog/'+kind,base,[],quantityEditor?values=>{
      const body={...base,...Object.fromEntries(values)};
      body.active=body.active==='true';
      if(body.campus_id||body.new_campus||body.quantity!==''){body.inventory={campus_id:Number(body.campus_id),new_campus:body.new_campus,quantity:body.quantity===''?null:Number(body.quantity),version:campuses.find(c=>String(c.id)===body.campus_id)?.stock_version||0,reason:body.reason};}
      delete body.campus_id;delete body.new_campus;delete body.quantity;return body;
    }:null);
    if(operation.recovery)return;
    if(quantityEditor){operation.campuses=campuses;operation.editInventory=true;updateCampusBalance();}
    if($('asset-edit-units'))$('asset-edit-units').onclick=()=>{dialog.close();window.AssetUnits.show(value.id);};
  }
  function directoryManager(kind){
    const category=kind==='categories',noun=category?'类别':'校区',action=category?'category':'campus';
    opener=document.activeElement;operation=null;$('asset-dialog-title').textContent=noun+'管理';$('asset-form-status').textContent='';
    $('asset-fields').innerHTML=catalog[kind].map(c=>`<div class="asset-heading"><span>${esc(c.name)}${c.active?'':'（停用）'}</span>${button('edit-'+action,'编辑',c.id)}</div>`).join('')+button('new-'+action,'新增'+noun,0);
    $('asset-save').hidden=true;dialog.showModal();
  }
  async function act(action,id){
    if(action==='new-campus'||action==='edit-campus'){dialog.close();catalogEditor('campuses',catalog.campuses.find(c=>c.id===id)||{});return;}
    if(action==='new-category'||action==='edit-category'){dialog.close();catalogEditor('categories',catalog.categories.find(c=>c.id===id)||{});return;}
    if(action==='units'){await window.AssetUnits.show(id);return;}
    if(action==='print-item'){window.AssetLabels.open([detail?.item||listing.find(i=>i.id===id)]);return;}
    if(action==='edit-item'){catalogEditor('items',detail?.item||listing.find(i=>i.id===id));return;}
    if((action==='register'||action==='quantity')&&listing.find(i=>i.id===id)?.tracking==='unit'){await window.AssetUnits.show(id);return;}
    if(action==='register'||action==='quantity'){
      pending=true;const trigger=document.activeElement;status('正在读取校区库存…');
      try{
        const data=await api('items/'+id);
        const eligible=data.campuses.filter(c=>c.active);
        if(!data.item.active||!eligible.length)throw Error('该物品已停用或没有可用校区');
        const selected=eligible.find(c=>String(c.id)===filter.elements.campus_id.value)?.id||(eligible.length===1?eligible[0].id:'');
        const type=action==='quantity'?'adjust':data.item.kind==='returnable'?'borrow':'consume';
        open(action==='quantity'?'调整数量':names[type]+'登记',
          `<p>${esc(data.item.name)} · ${esc(data.item.specification)} · 单位：${esc(data.item.unit)}</p>`+
          `<label>校区<select name="campus_id" required>${options(eligible,selected,'请选择校区')}</select></label><p id="asset-campus-balance" role="status"></p>`+
          input('quantity',type==='adjust'?'调整后的在库数量':'数量',type==='adjust'?'':1,`type="number" inputmode="numeric" min="${type==='adjust'?0:1}" max="1000000000" step="1" required`)+
          timeField()+reason(type==='adjust'),'operations',{action:type,item_id:id},['quantity','campus_id']);
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
    let fields=`<p>${esc(detail.item.name)} · ${esc(campus.name)} · 在库 ${campus.available} ${esc(detail.item.unit)}</p>`+
      input('quantity',action==='adjust'?'盘点后的在库数量':'数量',action==='adjust'?campus.available:1,
        `type="number" inputmode="numeric" min="${action==='adjust'?0:1}" max="1000000000" step="1" required`);
    if(action==='transfer')fields+=`<label>调入校区<select name="target_campus_id" required>${options(catalog.campuses.filter(c=>c.active&&c.id!==id),'','请选择调入校区')}</select></label>`;
    fields+=timeField()+reason(['receive','adjust','transfer'].includes(action));
    open(names[action],fields,'operations',base,['quantity','target_campus_id']);
  }
  function updateCampusBalance(){
    if(!operation?.campuses)return;
    const campus=operation.campuses.find(c=>String(c.id)===form.elements.campus_id.value);
    $('asset-campus-balance').textContent=campus?`在库 ${campus.available} · 借出未还 ${campus.borrowed}`:'请先选择实际操作校区。';
    if(operation.base.action==='adjust'||operation.editInventory)form.elements.quantity.value=campus?campus.available:'';
  }
  form.addEventListener('change',e=>{if(e.target.name==='campus_id')updateCampusBalance();});
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(pending||!operation)return;
    const recovery=!!operation.recovery;
    let body=recovery?unresolved?.body:{...operation.base};
    if(!body)return;
    if(!recovery){
      for(const [key,value] of new FormData(form))body[key]=operation.numberKeys.includes(key)?Number(value):['active','detached','destroyed'].includes(key)?value==='true':key==='serials'?(value.trim()?value.split('\n').map(s=>s.trim()):undefined):value;
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
      try{rememberPending(saved);}
      catch(_){
        $('asset-form-status').textContent='无法安全保存待确认请求，请检查浏览器会话存储设置后重试。';
        return;
      }
    }
    pending=true;$('asset-save').disabled=true;$('asset-close').disabled=true;$('asset-form-status').textContent='正在保存…';
    try{
      await api(saved.path,{...saved.body,request_id:saved.request_id});
      forgetPending();
      document.dispatchEvent(new CustomEvent("asset-operation-saved",{detail:saved}));
      dialog.close();opener?.focus();const refreshed=await load();if(refreshed)status('已保存。');
    }catch(error){
      const unknown=!error.businessRejected;
      if(!unknown)forgetPending();
      $('asset-form-status').textContent=error.message+(unknown?'；结果尚未确认，可重试原请求；刷新或重新打开页面后仍会保留该请求。':'');
      $('asset-form-status').focus();
    }finally{pending=false;$('asset-save').disabled=false;$('asset-close').disabled=false;}
  });
  dialog.addEventListener('cancel',e=>{if(pending)e.preventDefault();});
  $('asset-close').onclick=()=>{if(!pending){dialog.close();opener?.focus();}};
  document.addEventListener('click',e=>{
    if(pending)return;
    const b=e.target.closest('[data-action]');
    if(b)act(b.dataset.action,Number(b.dataset.id));
    else if(!e.target.closest('a,button,input,select,textarea')){
      const card=e.target.closest('[data-register]');
      if(card){card.querySelector('[data-action=register]').focus();act('register',Number(card.dataset.register));}
    }
  });
  document.addEventListener('change',e=>{if(e.target.id==='asset-event-campus'){eventCampus=e.target.value;eventPage=1;load();}});
  $('asset-create').onclick=()=>{
    if(unresolved){recoverPending();return;}
    $('asset-add-guidance').textContent=catalog.can_manage?'可手动新建目录，或扫描标签匹配已有物品并核对入库。':'可扫码登记已有耗材；新物品建档需由管理人员确认。';
    $('asset-add-dialog').showModal();
  };
  $('asset-add-close').onclick=()=>{$('asset-add-dialog').close();$('asset-create').focus();};
  $('asset-add-manual').onclick=()=>{$('asset-add-dialog').close();catalogEditor('items');opener=$('asset-create');};
  $('asset-campuses').onclick=()=>directoryManager('campuses');
  $('asset-categories').onclick=()=>directoryManager('categories');
  $('asset-pending-retry').onclick=()=>recoverPending();
  $('asset-refresh').onclick=()=>load();
  filter.onsubmit=e=>{e.preventDefault();listPage=1;load();};
  filter.addEventListener('change',e=>{
    if(e.target.name==='campus_id'||e.target.name==='category'){listPage=1;load();}
  });
  window.AssetLedger={api,load,open,input,reason,options,esc,campusFields,get catalog(){return catalog;},get blocked(){return !!unresolved||pending;},get detail(){return detail;},get listing(){return listing;}};
  load();
})();
