(() => {
  'use strict';
  const core=window.AssetLedger,{api,esc,input,reason,options}=core,$=id=>document.getElementById(id),dialog=$('asset-lifecycle-dialog');
  let mode='objects',data=null,take=null;
  const btn=(action,label,id='')=>`<button type="button" class="btn" data-life="${action}" data-id="${id}">${label}</button>`;
  async function show(value){mode=value;dialog.showModal();await refresh();}
  async function refresh(){
    try{
      data=await api('units/catalog');const area=$('asset-lifecycle-content');
      if(mode==='objects'){
        area.innerHTML='<h3>机器人与测试平台</h3>'+btn('new','新增使用对象')+data.objects.map(o=>`<article><h4>${esc(o.name)}${o.active?'':'（停用）'}</h4>${btn('edit','编辑',o.id)}${btn('object-units','查看已装设备',o.id)}${core.catalog.can_manage?btn('object-move','整车移动',o.id):''}</article>`).join('');
      }else{
        area.innerHTML='<h3>小范围单件盘点</h3><p>冻结所选校区与型号，核对后解冻。未扫到的设备仅记待核实，不自动记为遗失。</p>'+btn('take-new','开始盘点')+data.takes.map(t=>`<p>#${t.id} · 校区 #${t.campus_id} · ${t.scope==='warehouse'?'仓库在库':'全量在册'} · ${t.status} ${btn('take-open','查看盘点',t.id)}</p>`).join('')+'<p>显示最近 50 次盘点。历史编号：<input id="asset-take-number" type="number" min="1">'+btn('take-lookup','打开历史盘点')+'</p><div id="asset-take-detail"></div>';
        if(take)await detail(take.take.id);
      }
    }catch(e){$('asset-lifecycle-status').textContent=e.message;}
  }
  async function detail(id){
    take=await api('units/stocktakes/'+id);
    $('asset-take-detail').innerHTML=`<h4>盘点 #${id} · ${take.take.status}</h4><p>已核对 ${take.units.filter(u=>u.seen_at).length} / ${take.units.length}；范围外观察 ${take.unexpected.length} 次</p><ul>${take.units.map(u=>`<li>U:${u.unit_id} · ${u.seen_at?'已核对':'待核实'}</li>`).join('')}</ul>`+(take.take.status==='open'?btn('take-scan','登记扫码清单中的实物')+btn('take-close','核对差异并解冻')+btn('take-cancel','取消盘点并解冻'):'');
  }
  async function objectUnits(id){
    const result=[];let page=1,total;
    do{const d=await api(`units?object_id=${id}&lifecycle=active&page=${page++}`);result.push(...d.units);total=d.total;}while(result.length<total&&result.length<=100);
    return result;
  }
  async function act(a,id){
    if(a==='new'||a==='edit'){
      const o=data.objects.find(v=>v.id===id)||{};
      core.open('使用对象',input('name','名称',o.name||'','required maxlength="120"')+`<label>关联造车组<select name="project_id">${options(data.projects,o.project_id,'不关联')}</select></label><label>状态<select name="active">${options([{id:'true',name:'启用'},{id:'false',name:'停用'}],o.active===0?'false':'true')}</select></label>`+reason(true),'units/objects',o.id?{id:o.id,version:o.version}:{},['project_id']);
    }else if(a==='object-units'){
      const units=await objectUnits(id);$('asset-lifecycle-status').textContent=units.map(u=>`U:${u.id} · ${u.item_name} · ${u.position} · ${u.keeper_name}`).join('；')||'尚未安装设备';
    }else if(a==='object-move'){
      const units=await objectUnits(id),o=data.objects.find(v=>v.id===id);
      if(!units.length||units.length>100)throw Error('整车需包含 1 至 100 件设备');
      core.open('核对整车移动',`<p>${esc(o.name)}：${units.map(u=>'U:'+u.id).join('、')}。任何装拆或保管变化都会要求重新核对。</p><label>目标校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label>`+input('location','实际位置','','required')+reason(true),'units/object-move',{object_id:id,version:o.version,units:units.map(u=>({id:u.id,version:u.version}))},['campus_id']);
    }else if(a==='take-new'){
      // A narrow selection is intentional: choose models from the current filtered page.
      const items=core.listing.filter(i=>i.tracking==='unit'&&i.active);
      core.open('选择盘点范围','<p>显示当前列表页的单件型号；可先通过资产列表搜索缩小范围。最多 20 个型号、1000 件。</p>'+items.map(i=>`<label><input type="checkbox" name="items" value="${i.id}"> ${esc(i.name)}</label>`).join('')+`<label>校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label><label>范围<select name="scope"><option value="warehouse">仓库在库</option><option value="all">全量在册（包含借出、装车）</option></select></label>`+reason(true),'units/stocktakes',{},[],f=>({item_ids:f.getAll('items').map(Number),campus_id:Number(f.get('campus_id')),scope:f.get('scope'),reason:f.get('reason')}));
    }else if(a==='take-lookup'){await detail(Number($('asset-take-number').value));
    }else if(a==='take-open'){await detail(id);
    }else if(a==='take-scan'){
      const ids=window.AssetUnits.selection.map(u=>u.id);if(!ids.length)throw Error('请先在扫码工作台选择“收集单件”或在单件抽屉勾选实物');
      core.open('核对盘点实物',`<p>盘点 #${take.take.id}，本次 ${ids.map(i=>'U:'+i).join('、')}。范围外单件记录为差异，不改变位置。</p>`+reason(true),'units/stocktakes/scan-batch',{take_id:take.take.id,unit_ids:ids});
    }else if(a==='take-close'||a==='take-cancel'){
      const missing=take.units.filter(u=>!u.seen_at).map(u=>'U:'+u.unit_id);
      core.open('确认盘点结束',`<p>未核实：${missing.join('、')||'无'}。解冻后另行核对与纠正，不自动记为遗失。</p>`+reason(true),'units/stocktakes/record',{take_id:take.take.id,action:a==='take-close'?'close':'cancel',revision:take.revision});
    }
  }
  $('asset-objects').onclick=()=>show('objects');$('asset-takes').onclick=()=>show('takes');
  $('asset-lifecycle-close').onclick=()=>dialog.close();
  dialog.addEventListener('click',e=>{const b=e.target.closest('[data-life]');if(b)act(b.dataset.life,Number(b.dataset.id)).catch(e=>$('asset-lifecycle-status').textContent=e.message);});
  document.addEventListener('assets-loaded',()=>{$('asset-objects').hidden=!core.catalog.can_manage;$('asset-takes').hidden=!core.catalog.can_manage;if(dialog.open)refresh();});
})();
