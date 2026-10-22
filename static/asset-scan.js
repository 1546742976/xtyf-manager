/* Scans identify first. Stock changes always use the ledger's explicit, idempotent confirmation. */
(() => {
  'use strict';
  const core=window.AssetLedger,$=id=>document.getElementById(id),dialog=$('asset-scan-dialog');
  const {esc,input,reason,options}=core;
  const taskNames={general:'扫一扫',search:'查找物料',receive:'扫码入库',borrow:'扫码借用',return:'扫码归还',count:'扫码盘点',move:'扫码调拨'};
  const hints={general:'扫描原包装或系统标签，识别后再选择要办的事。',search:'扫描嘉立创包装查询商品资料，并查找已关联的本地物料。',receive:'连续扫描采购包装，标签自动加入清单。扫完后统一核对数量和校区，再整批入库。',borrow:'按件管理的设备可连续扫描；按数量管理的物料识别后填写借用数量。',return:'扫描设备或物料标签，核对实物或未还借单后归还。',count:'按件管理先选择盘点任务；按数量管理识别后核对库存。',move:'扫描设备或物料，核对目标校区和实际位置后登记。'};
  const aliases={query:'general',receipts:'receive',units:'borrow'};
  const packages=new Map(),unitQueues=new Map();
  let task='general',context={},resolved=null,sequence=0,refreshAfterSave=false;
  let receiptLookups=Promise.resolve(),batchReason='';
  const status=message=>$('asset-scan-status').textContent=message;
  const button=(action,label,primary=false,extra='')=>`<button type="button" class="btn${primary?' primary':''}" data-scan-action="${action}" ${extra}>${label}</button>`;
  const officialUrl=code=>/^C[1-9][0-9]{0,17}$/.test(code||'')?'https://www.lcsc.com/product-detail/'+code+'.html':'';
  const currentPackages=()=>[...packages.values()].filter(value=>!context.itemId||value.scopeItemId===Number(context.itemId)||value.item?.id===Number(context.itemId));
  function feedback(kind,title,message='',audible=false){
    document.dispatchEvent(new CustomEvent('asset-scan-feedback',{detail:{kind,title,message,audible,task,count:task==='receive'?currentPackages().length:currentUnits().size}}));
  }
  function receiptIssue(value){
    if(value.state==='loading')return '正在查询资料';
    if(value.error)return value.error;
    if(!value.item)return '待关联本地物料';
    if(value.scopeItemId&&value.item.id!==value.scopeItemId)return '与当前入库物料不一致';
    if(!value.item.active)return '物料已停用';
    if(['status','migrating','legacy'].includes(value.item.tracking))return '当前管理方式不支持包装入库';
    return '';
  }
  function refreshReceipt(entry){
    const token=(entry.lookup||0)+1;entry.lookup=token;entry.state='loading';entry.error='';
    receiptLookups=receiptLookups.then(async()=>{
      if(packages.get(entry.parsed.raw)!==entry||entry.lookup!==token)return;
      try{
        const value=await core.api('scan/resolve',{raw:entry.parsed.raw});
        if(packages.get(entry.parsed.raw)!==entry||entry.lookup!==token)return;
        Object.assign(entry,value,{state:'ready'});
        if(value.parsed.type!=='package')entry.error='请使用采购包装二维码；物料编号或单件标签不能作为包装批次入库';
        if(task==='receive'&&dialog.open&&currentPackages().includes(entry)){
          const issue=receiptIssue(entry);
          feedback(issue?'attention':'success',entry.item?.name||entry.supplier_lookup?.product?.name||entry.parsed.code||'待处理标签',issue||`标签数量 ${entry.parsed.label_quantity??'待填写'} · 已加入清单`);
        }
      }catch(error){
        if(packages.get(entry.parsed.raw)!==entry||entry.lookup!==token)return;
        entry.state='error';entry.error=error.message;
        if(task==='receive'&&dialog.open)feedback('error','此条查询失败','标签已保留，可以在清单中重试');
      }
      if(task==='receive'){queue();if(resolved===entry&&!entry.error)renderResult(entry);}
    });
    if(task==='receive')queue();
    return receiptLookups;
  }
  function collectReceipt(raw){
    raw=typeof raw==='string'?raw.trim():'';
    if(!raw||raw.length>4096){status('标签内容为空或过长');feedback('error','未加入清单','请重新扫描有效包装标签',true);return false;}
    if(packages.has(raw)){status('同一标签已在清单中，不重复累计数量。');feedback('duplicate','已扫描过','此标签已在清单中，未重复增加',true);return false;}
    if(currentPackages().length>=20){status('每批最多 20 个包装，请先核对入库。');feedback('attention','本批已满 20 个','请先核对整批入库',true);return false;}
    const entry={parsed:{raw},item:null,state:'loading',scopeItemId:Number(context.itemId)||null,campusId:context.campusId||''};
    packages.set(raw,entry);$('asset-scan-raw').value='';
    refreshReceipt(entry);status(`已收集 ${currentPackages().length} 个标签，可继续扫描；查询结果会更新到清单。`);
    feedback('accepted','已读到标签','已加入清单，正在查询资料；请继续扫描',true);
    return true;
  }
  const currentUnits=()=>{
    const key=task+':'+(task==='count'?context.takeId||'':context.itemId||'');
    if(!unitQueues.has(key))unitQueues.set(key,new Map());
    return unitQueues.get(key);
  };
  const unitsOutsideTake=units=>task==='count'&&Array.isArray(context.unitIds)?units.filter(u=>!context.unitIds.includes(u.id)):[];
  function clearResult(){sequence++;resolved=null;refreshAfterSave=false;$('asset-scan-preview').replaceChildren();}
  function setTask(value){
    task=taskNames[value]?value:'general';$('asset-scan-mode').value=task;
    if(task!=='count')for(const key of ['takeId','unitIds','itemIds','items','onSelected'])delete context[key];
    $('asset-scan-title').textContent=taskNames[task];queue();
    $('asset-scan-dialog').classList.toggle('asset-scan-receiving',task==='receive');
    const preview=$('asset-scan-preview'),list=$('asset-scan-queue');
    preview.parentNode.insertBefore(task==='receive'?list:preview,task==='receive'?preview:list);
    feedback('idle',taskNames[task],hints[task]);
  }
  function open(value='general',nextContext={}){
    clearResult();context={...nextContext};setTask(aliases[value]||value);
    core.showDialog(dialog);status(hints[task]+(context.takeId?' 当前盘点 #'+context.takeId+'。':''));
    $('asset-scan-raw').value='';
    if(!matchMedia('(pointer:coarse)').matches)$('asset-scan-raw').focus();
  }
  function close(){dialog.close();}
  function queue(){
    const host=$('asset-scan-queue');host.replaceChildren();
    if(task==='general'||task==='search')return;
    if(task==='receive'){
      const values=currentPackages(),waiting=values.filter(v=>v.state==='loading').length,issues=values.filter(v=>v.state!=='loading'&&receiptIssue(v)).length;
      host.innerHTML=`<section class="asset-scan-list"><div class="asset-heading"><div><h3>待入库包装 · ${values.length} / 20 个</h3><p class="asset-muted">${waiting?`${waiting} 个正在查询 · `:''}${issues?`${issues} 个待处理 · `:''}全部暂存，尚未入库。</p></div></div>`+
        (values.length?`<ol class="asset-scan-queue-list">${values.map((v,i)=>`<li><div><strong>${i+1}. ${esc(v.item?.name||v.supplier_lookup?.product?.name||v.parsed.model||v.parsed.code||'正在识别标签')}</strong><span class="asset-muted">${esc(receiptIssue(v)||`${v.parsed.code} · 实收 ${v.quantity??v.parsed.label_quantity??'待填写'} ${v.item.unit}${v.history?.length?' · 需核对历史入库':''}`)}</span></div><div class="asset-actions">${v.state==='loading'?'':v.error?button('retry-package','重试',false,`data-index="${i}"`):button('review-package',v.item?'查看':'处理',false,`data-index="${i}"`)}${button('remove-package','移除',false,`data-index="${i}"`)}</div></li>`).join('')}</ol>`:'<p class="asset-muted">对准下一张包装二维码，识别后会自动出现在这里。</p>')+
        `<div class="asset-actions">${button('batch-receive','扫完了，核对整批',true,values.length&&!waiting&&!issues?'':'disabled')}${button('continue-scan','继续扫码')}${button('clear-packages','清空清单',false,values.length?'':'disabled')}</div>${issues?'<p class="asset-muted">先处理未关联或异常的条目，再一起入库。</p>':''}</section>`;
      document.dispatchEvent(new CustomEvent('asset-scan-queue-updated',{detail:{task,count:values.length,waiting,issues,canSubmit:!!values.length&&!waiting&&!issues}}));
      return;
    }
    if(resolved?.item&&resolved.item.tracking!=='unit'&&!currentUnits().size)return;
    if(task==='count'&&!context.takeId){
      host.innerHTML='<div class="asset-form-context"><strong>逐件盘点需先确定范围</strong><p>选择或创建盘点任务后再连续扫码，已选清单只属于该次盘点。</p><div class="asset-actions">'+button('choose-count','选择 / 开始盘点')+'</div></div>';
      return;
    }
    const values=[...currentUnits().values()],outside=unitsOutsideTake(values),label=task==='count'?'核对并登记盘点':'核对并'+({borrow:'借用',return:'归还',move:'调拨'})[task];
    host.innerHTML=`<section class="asset-scan-list"><h3>${context.takeId?'盘点 #'+context.takeId+' · ':''}本次实物清单 · ${values.length} 件</h3><p class="asset-muted">连续扫描会加入当前任务清单，确认前不改变记录。${outside.length?'其中 '+outside.length+' 件不在盘点范围，将记录为范围外差异。':''}</p>`+
      (values.length?`<ul class="asset-scan-queue-list">${values.map(u=>`<li><div><strong>${esc(u.item_name||'实物')} · U:${u.id}</strong><span class="asset-muted">${esc(u.serial||'未填写厂家 SN')}${outside.some(v=>v.id===u.id)?' · 范围外实物':''}</span></div>${button('remove-unit','移除',false,`data-unit-id="${u.id}"`)}</li>`).join('')}</ul>`:'')+
      `<div class="asset-actions">${button('submit-units',label,true,values.length?'':'disabled')}${button('clear-units','清空本次清单',false,values.length?'':'disabled')}</div></section>`;
  }
  function eligibleUnit(action,unit){
    if(unit.lifecycle!=='active')return false;
    if(action==='borrow')return !unit.keeper_id&&!unit.object_id&&unit.health==='normal';
    if(action==='return')return !!unit.keeper_id&&(core.catalog.can_manage||unit.keeper_id===core.catalog.member_id);
    if(action==='move')return !unit.object_id&&(core.catalog.can_manage||unit.keeper_id===core.catalog.member_id);
    return action==='count'&&core.catalog.can_manage;
  }
  function addUnit(value){
    if(task==='count'&&!context.takeId)throw Error('请先选择本次盘点任务，再扫描设备。');
    if(!eligibleUnit(task,value.unit))throw Error('该实物当前状态或权限不适合'+taskNames[task].replace('扫码','')+'，请查看实物记录。');
    const units=currentUnits();
    if(units.has(value.unit.id))throw Error('此实物已经在本次清单中，不会重复加入。');
    if(units.size>=100)throw Error('每批最多 100 件，请先核对当前清单。');
    units.set(value.unit.id,value.unit);queue();
    return '已加入本次实物清单，确认前不改变记录。';
  }
  function productCard(value){
    const p=value.parsed,lookup=value.supplier_lookup,url=officialUrl(p.code);
    if(!url)return '';
    const product=lookup?.product;
    return `<section class="asset-scan-result-card"><div class="asset-heading"><div><span class="asset-tag">嘉立创商品资料</span><h3>${esc(product?.name||p.model||p.code)}</h3></div><a class="btn" href="${url}" target="_blank" rel="noopener noreferrer">商城商品页 ↗</a></div>`+
      `<dl class="asset-scan-facts"><div><dt>商品编号</dt><dd>${esc(p.code)}</dd></div><div><dt>厂家型号</dt><dd>${esc(product?.model||p.model||'未提供')}</dd></div>${product?.manufacturer?`<div><dt>制造商</dt><dd>${esc(product.manufacturer)}</dd></div>`:''}${product?.package?`<div><dt>封装</dt><dd>${esc(product.package)}</dd></div>`:''}${p.type==='package'?`<div><dt>包装数量</dt><dd>${p.label_quantity??'无效，请手填'}（仅供入库核对）</dd></div><div><dt>订单</dt><dd>${esc(p.order||'未提供')}</dd></div>`:''}</dl>`+
      `<p class="asset-muted">${esc(lookup?.status==='found'?'来源：'+(product.source||'LCSC 官方商品页')+(lookup.cached?' · 已缓存资料':''):lookup?.message||'暂未取得商城资料，仍可使用标签内容查找本地物料。')}</p></section>`;
  }
  function allowedActions(value){
    const item=value.item,unit=value.unit;
    if(!item?.active)return [];
    if(unit)return ['borrow','return','count','move'].filter(a=>eligibleUnit(a,unit));
    if(['status','migrating'].includes(item.tracking))return [];
    const result=['receive'];
    if(item.kind==='returnable')result.push('borrow','return');
    if(item.tracking!=='unit'||core.catalog.can_manage)result.push('count');
    if(core.catalog.can_manage)result.push('move');
    return result;
  }
  function renderResult(value){
    const p=value.parsed,item=value.item,matching=!context.itemId||!item||Number(context.itemId)===item.id;
    const primary=['general','search','receive'].includes(task)?null:task;
    const allowed=matching?allowedActions(value):[];
    let content=productCard(value);
    if(item){
      content+=`<section class="asset-scan-result-card"><span class="asset-tag">${value.unit?'已识别单件实物':'已匹配本地物料'}</span><h3>${esc(item.name)}${value.unit?' · U:'+value.unit.id:''}</h3><p class="asset-muted">${esc(item.specification||'未填写型号 / 规格')} · ${esc(item.unit||'个')} · ${item.tracking==='unit'?'逐件追踪':item.tracking==='status'?'自由取用':item.kind==='returnable'?'按数量借还':'按数量管理'}</p>`+
        (!item.active?'<p role="alert">此物料已停用，仅可查看资料与历史记录。</p>':'')+
        (!matching?'<p role="alert">扫描结果不属于当前操作的物料，请核对标签后重扫。</p>':'')+
        `<div class="asset-actions">${button('details','查看本地物料',task==='search')}${value.unit?button('unit-details','查看实物记录'):''}`+
        (task==='general'?allowed.map(a=>button(a,taskNames[a].replace('扫码',''))).join(''):primary&&allowed.includes(primary)?button(primary,value.unit?'核对本次清单':item.tracking==='unit'&&['borrow','return','move'].includes(primary)?'选择具体实物':taskNames[primary].replace('扫码','')+'并核对',true):'')+
        '</div>'+
        (primary&&!allowed.includes(primary)&&item.active&&matching?`<p class="asset-muted">当前结果不能直接办理${taskNames[primary].replace('扫码','')}。${value.unit?'请核对实物状态和操作权限。':item.tracking==='migrating'?'请先完成该物料的实物核对。':item.tracking==='status'?'自由取用物料只需维护充足 / 不足状态。':'请核对管理类型及权限。'}</p>`:'')+'</section>';
    }else{
      const candidates=(value.candidates||[]).filter(i=>i.active);
      content+=`<section class="asset-scan-result-card"><h3>尚未关联本地物料</h3><p class="asset-muted">${candidates.length?'找到 '+candidates.length+' 个相同型号的候选，请核对后关联。':'识别成功后可建立物料档案，或搜索已有物料进行关联。'}</p>`+
        (candidates.length?`<ul>${candidates.slice(0,5).map(i=>`<li>${esc(i.name)} · ${esc(i.specification||'')}</li>`).join('')}</ul>`:'')+
        (value.can_manage?`<div class="asset-actions">${button('create','建立物料档案',true)}${button('bind','关联已有物料')}</div>`:'<p>请联系管理人员关联编号或建档。</p>')+'</section>';
    }
    if(value.history?.length)content+=`<details class="asset-form-section"><summary>此包装已有 ${value.history.length} 条近期入库登记</summary><p>再次入库必须填写重复收货原因，请先核对是否为同一包装。</p><ul>${value.history.map(h=>`<li>#${h.id} · ${esc(h.created_at)} · ${esc(h.actor_name)} · 实收 ${h.actual_quantity}</li>`).join('')}</ul></details>`;
    $('asset-scan-preview').innerHTML=content;
  }
  async function resolve(raw){
    if(task==='receive')return collectReceipt(raw);
    const own=++sequence;resolved=null;$('asset-scan-preview').replaceChildren();status('正在识别标签并查询资料…');
    try{
      const value=await core.api('scan/resolve',{raw});if(own!==sequence)return false;
      resolved=value;renderResult(value);queue();
      let message=value.parsed.quantity_error||'已识别，库存尚未改变。';
      if(context.itemId&&value.item&&Number(context.itemId)!==value.item.id)message='扫描结果不是当前物料，请核对标签后重扫。';
      else if(value.unit&&value.item?.active&&['borrow','return','count','move'].includes(task)){
        if(task==='count'&&!context.takeId)message='已找到实物。请选择盘点任务，再扫描加入该任务。';
        else message=addUnit(value);
      }
      status(message);$('asset-scan-raw').value='';feedback('success',value.item?.name||value.supplier_lookup?.product?.name||value.parsed.code||'识别成功',message,true);return true;
    }catch(error){if(own===sequence){status(error.message);feedback('error','识别未完成',error.message,true);}return false;}
  }
  function receive(value){
    if(value.item.tracking==='unit'){
      core.open('新增实物 · '+value.item.name,`<div class="asset-operation-summary"><strong>${esc(value.item.name)}</strong><p>本次将新增独立的实物编号，请填写实际收到的件数。</p></div>`+
        `<label>实际校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),context.campusId||'','请选择')}</select></label>`+input('quantity','实收件数',1,'type="number" inputmode="numeric" min="1" max="100" step="1" required')+
        '<label>厂家 SN（选填，一件一行）<textarea name="serials" rows="3"></textarea></label>'+reason(true),'units/receive',{item_id:value.item.id},['quantity','campus_id']);
      return;
    }
    return core.scanQuantity('receive',value.item.id,context);
  }
  function create(value){
    const p=value.parsed,product=value.supplier_lookup?.product;
    const fields='<p class="asset-form-context">保存只建立档案并关联商品编号，不增加库存。'+(packages.has(p.raw)?'包装仍保留在待入库清单中，稍后统一核对入库。':'')+'</p>'+
      input('name','物料名称',(product?.name||p.model||'').slice(0,120),'required maxlength="120"')+
      input('specification','厂家型号',(product?.model||p.model||'').slice(0,120),'maxlength="120"')+
      `<p class="asset-muted">商品编号：${esc(p.code||'')}</p>`+input('unit','计量单位','个','required maxlength="12"')+
      '<label>物料用途<select name="kind"><option value="consumable">耗材（使用后无需归还）</option><option value="returnable">设备 / 工具（借用后归还）</option></select></label>'+
      '<label id="scan-create-tracking" hidden>设备如何管理<select name="returnable_mode" disabled><option value="unit">逐件追踪，选择具体实物借还</option><option value="quantity">按数量借还，无需逐件贴码</option></select></label>';
    core.open('确认物料档案',fields,'catalog/items',{supplier_code:p.code});
    const kind=$('asset-form').elements.kind,tracking=$('scan-create-tracking');
    if(kind&&tracking)kind.addEventListener('change',()=>{tracking.hidden=kind.value!=='returnable';tracking.querySelector('select').disabled=tracking.hidden;});
  }
  function bind(value){
    const p=value.parsed,seed=value.supplier_lookup?.product?.model||p.model||p.code;
    const candidates=(value.candidates||[]).filter(i=>i.active&&i.tracking!=='status');
    core.open('关联已有物料',`<p class="asset-form-context">把 ${esc(p.code)} 关联到确认无误的本地物料。保存关联不增加库存。</p><label>搜索名称或型号<input id="scan-bind-query" value="${esc(seed)}" maxlength="120"></label><div class="asset-actions"><button type="button" class="btn" id="scan-bind-search">搜索物料</button></div><p id="scan-bind-status" role="status">${candidates.length?'已找到 '+candidates.length+' 个同型号候选。':'输入名称或型号，搜索已有物料。'}</p><label>本地物料<select name="item_id" required>${options(candidates.map(i=>({...i,name:i.name+(i.specification?' · '+i.specification:'')})),'','请选择')}</select></label>`+reason(true),'identifiers',{code:p.code},['item_id']);
    const search=$('scan-bind-search');if(!search)return;
    let searchSequence=0;
    const run=async()=>{
      const own=++searchSequence,field=$('scan-bind-query'),message=$('scan-bind-status'),select=$('asset-form').elements.item_id;
      try{
        message.textContent='正在搜索…';
        const found=await core.api('items?q='+encodeURIComponent(field.value.trim()));
        if(own!==searchSequence||!search.isConnected)return;
        const values=found.items.filter(i=>i.active&&!['status','migrating'].includes(i.tracking));
        select.innerHTML=options(values.map(i=>({...i,name:i.name+(i.specification?' · '+i.specification:'')})),'','请选择');
        message.textContent=values.length?'找到 '+values.length+' 个候选，请核对型号后选择。':'没有找到可关联物料，可修改关键词或返回建档。';
      }catch(error){if(own===searchSequence&&message.isConnected)message.textContent=error.message;}
    };
    search.onclick=run;$('scan-bind-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();run();}};
    if(!candidates.length)run();
  }
  function batchReceive(){
    const values=currentPackages();if(!values.length)throw Error('待入库包装清单为空。');
    if(values.length>20)throw Error('每批最多登记 20 个包装，请移除多余包装，或从具体物料的扫码入库入口分批核对。');
    if(values.some(receiptIssue))throw Error('请等待查询完成，并处理清单中未关联或异常的条目。');
    const campuses=core.catalog.campuses.filter(c=>c.active),defaultCampus=context.campusId||(campuses.length===1?campuses[0].id:'');
    const fields=values.map((v,i)=>`<fieldset class="asset-form-section"><legend>${esc(v.item.name)} · ${esc(v.parsed.code)}</legend><p class="asset-muted">标签数量：${v.parsed.label_quantity??'无效'}</p>`+
      input('q'+i,'实收数量',v.quantity??v.parsed.label_quantity??'',`type="number" inputmode="numeric" min="1" max="${v.item.tracking==='unit'?100:1000000000}" step="1" required`)+
      `<label>校区<select name="c${i}" required>${options(campuses,v.campusId||defaultCampus,'请选择')}</select></label>`+
      (v.history?.length?`<label>重复登记核对原因<textarea name="d${i}" required maxlength="500">${esc(v.duplicateReason||'')}</textarea></label>`:'')+'</fieldset>').join('');
    core.open('核对整批包装',`<div class="asset-operation-summary"><strong>本次登记 ${values.length} 个包装</strong><p>逐个核对实收数量与实际校区，确认保存后整批入库。</p></div><label>统一设置入库校区<select id="scan-batch-campus">${options(campuses,'','可统一选择，也可逐条设置')}</select></label>`+fields+reason(false),'scan/batch',{},[],form=>({receipts:values.map((v,i)=>({raw:v.parsed.raw,item_id:v.item.id,quantity:Number(form.get('q'+i)),campus_id:Number(form.get('c'+i)),receipt_version:v.receipt_version,duplicate_reason:form.get('d'+i)||'',reason:form.get('reason')}))}));
    const common=$('scan-batch-campus');if(!common)return;
    const form=$('asset-form');form.elements.reason.value=batchReason;
    for(const [i,entry] of values.entries())for(const [prefix,key] of [['q','quantity'],['c','campusId'],['d','duplicateReason']]){
      const field=form.elements[prefix+i];if(field)field.oninput=()=>{entry[key]=field.value;};
    }
    form.elements.reason.oninput=()=>{batchReason=form.elements.reason.value;};
    common.onchange=()=>{if(common.value)values.forEach((entry,i)=>{form.elements['c'+i].value=common.value;entry.campusId=common.value;});};
    $('asset-save').textContent=`确认整批入库（${values.length} 个包装）`;
  }
  async function submitUnits(){
    const values=[...currentUnits().values()];if(!values.length)throw Error('请先扫描实物加入本次清单。');
    if(task==='count'){
      if(!context.takeId)throw Error('请先选择盘点任务。');
      if(context.onSelected){await context.onSelected(values);close();return;}
      const outside=unitsOutsideTake(values);
      core.open('核对盘点实物',`<div class="asset-operation-summary"><strong>盘点 #${context.takeId} · 本次 ${values.length} 件</strong><p>${values.map(u=>'U:'+u.id).join('、')}</p><p>请确认已在现场核对这些实物。${outside.length?'其中 '+outside.map(u=>'U:'+u.id).join('、')+' 在盘点范围外，将单独记录为差异，不改变位置。':''}</p></div>`+reason(true),'units/stocktakes/scan-batch',{take_id:context.takeId,unit_ids:values.map(u=>u.id)});
    }else await window.AssetUnits.scanOperate(task,values,context);
  }
  async function act(action,element){
    if(action==='open-list'){$('asset-camera-stop')?.click();$('asset-scan-queue').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});return;}
    if(action==='continue-scan'){
      if(!$('asset-scan-camera').hidden){$('asset-scan-camera').scrollIntoView({block:'start'});$('asset-camera-start')?.click();}
      else $('asset-scan-raw').focus();
      return;
    }
    if(action==='retry-package'||action==='review-package'){
      const value=currentPackages()[Number(element.dataset.index)];if(!value)return;
      if(action==='retry-package'){refreshReceipt(value);return;}
      resolved=value;renderResult(value);$('asset-scan-preview').scrollIntoView({block:'start'});return;
    }
    if(action==='remove-package'){const value=currentPackages()[Number(element.dataset.index)];if(value){packages.delete(value.parsed.raw);if(resolved===value)clearResult();}queue();return;}
    if(action==='clear-packages'){for(const value of currentPackages()){packages.delete(value.parsed.raw);if(resolved===value)clearResult();}if(!packages.size)batchReason='';queue();return;}
    if(action==='batch-receive'){batchReceive();return;}
    if(action==='remove-unit'){currentUnits().delete(Number(element.dataset.unitId));queue();return;}
    if(action==='clear-units'){currentUnits().clear();queue();return;}
    if(action==='submit-units'){await submitUnits();return;}
    if(action==='choose-count'){await window.AssetLifecycle.openCount();return;}
    if(!resolved)return;
    const value=resolved,item=value.item;
    if(action==='details'){location.href='/assets/detail?id='+item.id;return;}
    if(action==='unit-details'){await window.AssetUnits.showRecord(item.id,value.unit.id);return;}
    if(action==='create'){create(value);return;}
    if(action==='bind'){bind(value);return;}
    if(!item?.active)throw Error('此物料已停用，仅可查看资料。');
    if(context.itemId&&Number(context.itemId)!==item.id)throw Error('扫描结果不属于当前操作物料。');
    if(!allowedActions(value).includes(action))throw Error('当前物料状态或权限不允许此操作。');
    if(action==='receive'){
      if(value.parsed.type==='package'){setTask('receive');$('asset-scan-preview').replaceChildren();collectReceipt(value.parsed.raw);return;}
      await receive(value);return;
    }
    if(value.unit){
      if(task!==action){setTask(action);renderResult(value);}
      if(action==='count'&&!context.takeId){await window.AssetLifecycle.openCount();return;}
      if(!currentUnits().has(value.unit.id))addUnit(value);
      await submitUnits();return;
    }
    if(item.tracking==='unit'){
      if(action==='count'){await window.AssetLifecycle.openCount();return;}
      await window.AssetUnits.show(item.id);status('已打开具体实物清单，请选择要'+taskNames[action].replace('扫码','')+'的实物。');return;
    }
    await core.scanQuantity(action,item.id,context);
  }
  $('asset-scan')?.addEventListener('click',()=>open('receive'));
  $('asset-scan-close').onclick=close;
  $('asset-scan-mode').onchange=()=>{clearResult();setTask($('asset-scan-mode').value);status(hints[task]);};
  $('asset-scan-form').onsubmit=e=>{e.preventDefault();resolve($('asset-scan-raw').value);};
  $('asset-scan-raw').onkeydown=e=>{if(e.key===$('asset-scan-end').value&&!e.shiftKey&&!e.isComposing){e.preventDefault();resolve(e.target.value);}};
  dialog.addEventListener('click',e=>{const target=e.target.closest('[data-scan-action]');if(target)act(target.dataset.scanAction,target).catch(error=>status(error.message));});
  dialog.addEventListener('close',()=>{sequence++;});
  dialog.addEventListener('asset-dialog-resumed',()=>{
    queue();
    if(refreshAfterSave&&resolved){refreshAfterSave=false;if(task==='receive')renderResult(resolved);else resolve(resolved.parsed.raw);}
  });
  document.addEventListener('click',e=>{
    const target=e.target.closest('[data-asset-scan-task]');if(!target)return;
    e.preventDefault();open(target.dataset.assetScanTask,{itemId:Number(target.dataset.itemId)||undefined,campusId:Number(target.dataset.campusId)||undefined,takeId:Number(target.dataset.takeId)||undefined});
  });
  document.addEventListener('asset-operation-saved',e=>{
    const {path,body}=e.detail;
    if(path==='scan/batch'){
      for(const receipt of body.receipts)packages.delete(receipt.raw);
      batchReason='';clearResult();queue();status('整批包装已入库，可以继续扫描。');feedback('success','整批入库完成',`本次 ${body.receipts.length} 个包装已保存`,true);
    }else if(['scan/receive','scan/create-receive'].includes(path)){
      packages.delete(body.raw);clearResult();queue();$('asset-scan-raw').value='';status('包装已入库，可以继续扫描。');
    }else if(path==='units/operate'){
      for(const entries of unitQueues.values())for(const unit of body.units)if(entries.get(unit.id)?.version===unit.version)entries.delete(unit.id);
      clearResult();queue();status('实物操作已保存，可以继续扫描。');
    }else if(path==='units/stocktakes/scan-batch'){
      const entries=unitQueues.get('count:'+body.take_id);if(entries)for(const id of body.unit_ids)entries.delete(id);
      clearResult();queue();status('本批盘点已登记，可以继续扫描。');
    }else if(path==='identifiers'||path==='catalog/items'){
      const code=String(body.code||body.supplier_code||'').trim().toUpperCase();
      for(const entry of packages.values())if(code&&entry.parsed.code===code)refreshReceipt(entry);
      refreshAfterSave=!!resolved;
    }
    else if(path==='units/receive'){clearResult();status('实物已入库，可以继续扫描。');}
    else if(path==='operations'){clearResult();status('操作已保存，可以继续扫描。');}
  });
  window.AssetScan={open,resolve,dialog,get resolved(){return resolved;},get task(){return task;}};
})();
