(() => {
  'use strict';
  const core=window.AssetLedger,{api,esc,input,reason,options}=core,$=id=>document.getElementById(id),dialog=$('asset-lifecycle-dialog');
  let mode='objects',data=null,take=null;
  const btn=(action,label,id='',extra='')=>`<button type="button" class="btn" data-life="${action}" data-id="${id}" ${extra}>${label}</button>`;
  const campusName=id=>core.catalog.campuses.find(c=>c.id===id)?.name||'未知校区';
  const takeStatus=value=>({open:'盘点中 · 已冻结',closed:'已完成',cancelled:'已取消'}[value]||'状态待核对');
  const status=message=>$('asset-lifecycle-status').textContent=message;
  async function show(value){mode=value;$('asset-lifecycle-title').textContent=value==='objects'?'使用对象与已装设备':'实物盘点';core.showDialog(dialog);await refresh();}
  async function openCount(id=null){
    const scanner=window.AssetScan?.dialog;
    if(scanner?.open){
      const closed=new Promise(resolve=>scanner.addEventListener('close',resolve,{once:true}));
      scanner.close();await closed;
    }
    take=null;await show('takes');
    if(id)await detail(Number(id));
  }
  async function refresh(){
    try{
      status('正在读取…');
      data=await api('units/catalog');const area=$('asset-lifecycle-content');
      if(mode==='objects'){
        area.innerHTML='<div class="asset-heading"><p class="asset-form-context">按机器人或测试平台查看安装设备，整机搬迁时统一登记位置。</p>'+btn('new','新增使用对象')+'</div>'+(data.objects.length?data.objects.map(o=>`<article class="asset-directory-row"><div><h3>${esc(o.name)}</h3><p class="asset-unit-meta">${o.active?'启用中':'已停用'} · ${esc(data.projects.find(p=>p.id===o.project_id)?.name||'未关联造车组')}</p></div><div class="asset-actions">${btn('object-units','查看已装设备',o.id)}${core.catalog.can_manage?btn('object-move','整机移动',o.id):''}${btn('edit','编辑对象',o.id)}</div></article>`).join(''):'<p class="asset-empty">还没有使用对象。新建机器人或测试平台后，可在单件操作中安装设备。</p>')+'<div id="asset-object-detail"></div>';
      }else{
        area.innerHTML='<div class="asset-heading"><div><h3>先选范围，再扫码核对</h3><p class="asset-form-context">开始后冻结所选校区与型号。完成盘点后解冻，未扫到的设备只标记为待核实。</p></div>'+btn('take-new','开始新盘点')+'</div>'+(data.takes.length?data.takes.map(t=>`<article class="asset-directory-row"><div><h3>${esc(campusName(t.campus_id))} · 盘点 #${t.id}</h3><p class="asset-unit-meta">${t.scope==='warehouse'?'仓库在库':'全量在册'} · ${takeStatus(t.status)}</p><p class="asset-unit-meta">开始于 ${esc(t.created_at)}</p></div><div class="asset-actions">${btn('take-open',t.status==='open'?'继续盘点':'查看结果',t.id)}</div></article>`).join(''):'<p class="asset-empty">暂无盘点记录，点击「开始新盘点」选择校区和物料型号。</p>')+'<details><summary>查找更早的盘点记录</summary><p class="asset-muted">上方显示最近 50 次盘点。</p><label>盘点编号<input id="asset-take-number" type="number" min="1" inputmode="numeric"></label>'+btn('take-lookup','打开历史盘点')+'</details><div id="asset-take-detail"></div>';
        if(take)await detail(take.take.id);
      }
      status('');
    }catch(e){status(e.message);}
  }
  async function detail(id){
    if(!Number.isInteger(id)||id<1)throw Error('请填写有效的盘点编号');
    take=await api('units/stocktakes/'+id);
    const seen=take.units.filter(u=>u.seen_at).length;
    const area=$('asset-take-detail');area.className='asset-section';area.tabIndex=-1;
    area.innerHTML=`<div class="asset-operation-summary"><h3>${esc(campusName(take.take.campus_id))} · 盘点 #${id}</h3><p>${takeStatus(take.take.status)} · ${take.take.scope==='warehouse'?'仓库在库':'全量在册'}</p><p>已核对 ${seen} / ${take.units.length} 件 · 待核实 ${take.units.length-seen} 件 · 范围外观察 ${take.unexpected.length} 次</p></div>`+
      (take.take.status==='open'?`<div class="asset-form-context"><strong>扫码核对本次盘点</strong><p>扫描设备标签后，在扫码页核对并登记到盘点 #${id}。本次扫码清单与借还选择分开保存；范围外实物会明确标为差异。</p><div class="asset-actions">${btn('take-start-scan','扫码盘点')}</div></div>`:'')+
      `<details><summary>查看盘点实物明细 · ${take.units.length} 件</summary>${take.units.length?take.units.map(u=>`<div class="asset-directory-row"><div><strong>U:${u.unit_id}${u.snapshot.serial?' · SN '+esc(u.snapshot.serial):''}</strong><p class="asset-unit-meta">${esc(u.snapshot.location||'未填写存放位置')}</p></div><span>${u.seen_at?'已核对':'待核实'}</span></div>`).join(''):'<p class="asset-muted">所选范围内没有在册设备。</p>'}</details>`+
      (take.take.status==='open'?`<div class="asset-actions asset-section">${btn('take-close','核对差异并完成盘点')}${btn('take-cancel','取消本次盘点')}</div><p class="asset-muted">完成或取消均会解冻。待核实设备不会自动记为遗失。</p>`:'');
  }
  async function objectUnits(id){
    const result=[];let page=1,total;
    do{const d=await api(`units?object_id=${id}&lifecycle=active&page=${page++}`);result.push(...d.units);total=d.total;if(!d.units.length)break;}while(result.length<total&&result.length<=100);
    return result;
  }
  async function stocktakeItems(){
    const result=[];let page=1,total=0;
    do{const d=await api('items?view=equipment&page='+page++);result.push(...d.items);total=d.total;if(!d.items.length)break;}while(result.length<total);
    return result.filter(i=>i.tracking==='unit'&&i.active);
  }
  async function act(a,id){
    if(a==='new'||a==='edit'){
      const o=data.objects.find(v=>v.id===id)||{};
      core.open(o.id?'编辑使用对象 · '+o.name:'新增使用对象','<p class="asset-form-context">使用对象是安装设备的机器人或测试平台，建档后可在单件操作中选择。</p>'+input('name','对象名称',o.name||'','required maxlength="120"')+`<label>关联造车组<select name="project_id">${options(data.projects,o.project_id,'不关联')}</select></label><label>状态<select name="active">${options([{id:'true',name:'启用'},{id:'false',name:'停用'}],o.active===0?'false':'true')}</select></label>`+reason(true),'units/objects',o.id?{id:o.id,version:o.version}:{},['project_id']);
    }else if(a==='object-units'){
      const units=await objectUnits(id),o=data.objects.find(v=>v.id===id),area=$('asset-object-detail');area.className='asset-section';area.tabIndex=-1;
      area.innerHTML=`<h3>${esc(o.name)} · 已装设备</h3>`+(units.length?units.map(u=>`<article class="asset-directory-row"><div><strong>${esc(u.item_name)} · U:${u.id}</strong><p class="asset-unit-meta">${esc(u.campus_name||campusName(u.campus_id))} · ${esc(u.position||'未填写安装位置')}</p></div><span>${esc(u.keeper_name||'仓库保管')}</span></article>`).join('')+(units.length>100?`<p class="asset-muted">当前展示前 ${units.length} 件设备；整机移动仅支持不超过 100 件。</p>`:''):'<p class="asset-empty">此对象尚未安装设备。可在单件操作中选择「安装」。</p>');area.focus();area.scrollIntoView({block:'nearest'});
    }else if(a==='object-move'){
      const units=await objectUnits(id),o=data.objects.find(v=>v.id===id);
      if(!units.length||units.length>100)throw Error('整车需包含 1 至 100 件设备');
      core.open('登记整机移动 · '+o.name,`<div class="asset-operation-summary"><strong>${esc(o.name)} · 共 ${units.length} 件已装设备</strong><p>${units.map(u=>'U:'+u.id).join('、')}</p><p class="asset-muted">移动会更新全部已装设备的校区和位置；装拆或保管变化后需重新核对。</p></div><label>目标校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label>`+input('location','移动后的实际位置','','required')+reason(true),'units/object-move',{object_id:id,version:o.version,units:units.map(u=>({id:u.id,version:u.version}))},['campus_id']);
    }else if(a==='take-new'){
      status('正在读取可盘点的全部型号…');
      const items=await stocktakeItems();
      if(!dialog.open||mode!=='takes')return;
      if(!items.length)throw Error('暂无可盘点的单件型号，请先完成设备的实物建档。');
      status('');
      core.open('开始盘点 · 选择范围',`<p class="asset-form-context">可选全部 ${items.length} 个已启用的单件型号，不受列表筛选或翻页影响。每次选择 1–20 个型号，总范围最多 1000 件；保存后会冻结相应库存操作。</p><fieldset><legend>选择物料型号</legend>`+items.map(i=>`<label><input type="checkbox" name="items" data-take-item value="${i.id}" ${items.length===1?'checked':''}> ${esc(i.name)}${i.specification?' · '+esc(i.specification):''}</label>`).join('')+`<p id="asset-take-selection" class="asset-muted"></p></fieldset><label>盘点校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label><label>设备范围<select name="scope"><option value="warehouse">仓库在库</option><option value="all">全量在册（包含借出、装车）</option></select></label>`+reason(true),'units/stocktakes',{},[],f=>({item_ids:f.getAll('items').map(Number),campus_id:Number(f.get('campus_id')),scope:f.get('scope'),reason:f.get('reason')}));
      const choices=[...$('asset-fields').querySelectorAll('[data-take-item]')];
      if(choices.length){
        const validate=()=>{const count=choices.filter(c=>c.checked).length;choices[0].setCustomValidity(count<1?'请至少选择一个物料型号':count>20?'一次最多选择 20 个型号':'');$('asset-take-selection').textContent=`已选 ${count} / 20 个型号`;};
        choices.forEach(c=>c.addEventListener('change',validate));validate();
      }
    }else if(a==='take-lookup'){await detail(Number($('asset-take-number').value));
      $('asset-take-detail').focus();$('asset-take-detail').scrollIntoView({block:'nearest'});
    }else if(a==='take-open'){await detail(id);$('asset-take-detail').focus();$('asset-take-detail').scrollIntoView({block:'nearest'});
    }else if(a==='take-start-scan'){
      if(!take||take.take.status!=='open')throw Error('请选择仍在进行中的盘点');
      const itemIds=[...new Set(take.units.map(u=>u.snapshot.item_id))];
      window.AssetScan.open('count',{takeId:take.take.id,campusId:take.take.campus_id,itemIds,
        items:itemIds.map(id=>({id,name:take.units.find(u=>u.snapshot.item_id===id)?.snapshot.item_name||'物料 #'+id})),
        unitIds:take.units.map(u=>u.unit_id)});
    }else if(a==='take-close'||a==='take-cancel'){
      const missing=take.units.filter(u=>!u.seen_at).map(u=>'U:'+u.unit_id);
      core.open(a==='take-close'?'完成盘点并解冻':'取消盘点并解冻',`<div class="asset-operation-summary"><strong>${esc(campusName(take.take.campus_id))} · 盘点 #${take.take.id}</strong><p>待核实 ${missing.length} 件：${missing.join('、')||'无'}</p><p class="asset-muted">解冻后可恢复库存操作。待核实设备需另行核对与纠正，不会自动记为遗失。</p></div>`+reason(true),'units/stocktakes/record',{take_id:take.take.id,action:a==='take-close'?'close':'cancel',revision:take.revision});
    }
  }
  $('asset-objects').onclick=()=>show('objects');$('asset-takes').onclick=()=>show('takes');
  $('asset-lifecycle-close').onclick=()=>dialog.close();
  dialog.addEventListener('click',e=>{const b=e.target.closest('[data-life]');if(b)act(b.dataset.life,Number(b.dataset.id)).catch(e=>$('asset-lifecycle-status').textContent=e.message);});
  dialog.addEventListener('asset-dialog-resumed',()=>{if(mode==='takes'&&take)detail(take.take.id).catch(e=>status(e.message));});
  document.addEventListener('assets-loaded',()=>{$('asset-objects').hidden=!core.catalog.can_manage;$('asset-takes').hidden=!core.catalog.can_manage;if(dialog.open)refresh();});
  window.AssetLifecycle={openCount};
})();
