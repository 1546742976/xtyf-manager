/* A selection holds server versions. Failed batches remain selected for review. */
(() => {
  'use strict';
  const core=window.AssetLedger,{api,esc,input,reason,options}=core,$=id=>document.getElementById(id);
  const drawer=$('asset-unit-drawer'), selected=new Map();
  const labels={normal:'正常',inspection:'待检查',fault:'故障',repair:'维修中',active:'在册',lost:'遗失',retired:'报废',destroyed:'已销毁'};
  const actions={borrow:'借用',return:'归还',handoff:'发起责任移交',accept:'确认接收',cancel_handoff:'取消移交',move:'登记移动',install:'安装',detach:'拆卸',fault:'报障',repair:'送修',healthy:'确认恢复可借',retire:'报废',lost:'遗失结案',recover:'找回／误报恢复',correct:'纠正保管人'};
  let item=null,page=1,rows=[],meta=null,opener,sequence=0,mine=false;
  const btn=(action,label,id='')=>`<button type="button" class="btn" data-unit-action="${action}" data-id="${id}">${label}</button>`;
  const select=(name,label,values,value='',empty='请选择')=>`<label>${label}<select name="${name}" required>${options(values,value,empty)}</select></label>`;
  const campus=()=>select('campus_id','实际校区',core.catalog.campuses.filter(c=>c.active));
  const status=message=>$('asset-unit-status').textContent=message;
  function selectedSummary(){
    $('asset-unit-selected').textContent=`已选择 ${selected.size} 件（保留核对时的版本）`;
  }
  async function show(id=null,unitId=null,own=false){
    opener=document.activeElement;item=id?(await api('items/'+id)).item:null;mine=own;page=1;
    if(!drawer.open)drawer.showModal();
    await refresh();
    if(unitId){const u=rows.find(r=>r.id===unitId)||(await api('scan/resolve',{raw:'U:'+unitId})).unit;selected.set(u.id,u);selectedSummary();}
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
      $('asset-unit-campus').innerHTML=options(core.catalog.campuses,$('asset-unit-campus').value,'全部校区');
      $('asset-unit-content').innerHTML=(item?`<p><a href="/assets/detail?id=${item.id}">打开原详情与全部流水</a> · 总量由在册单件统计，报废、遗失不计入。</p>`:'')+
        (data.can_manage&&item?btn('receive','批量建档'):'')+
        '<div class="asset-grid">'+rows.map(u=>`<article class="asset-card"><label><input type="checkbox" data-unit-select="${u.id}" ${selected.has(u.id)?'checked':''}> 选择 U:${u.id}</label><h3>${esc(u.item_name)}</h3><p>${esc(u.serial||'未填写厂家 SN')} · ${esc(u.campus_name)}</p><p>${esc(u.location||'未填写位置')}</p><p>${labels[u.lifecycle]} · ${labels[u.health]} · ${esc(u.keeper_name||'仓库')}</p><p>${esc(u.object_name||'未安装')} ${esc(u.position)}</p>${u.receiver_id?`<p>待接收成员 #${u.receiver_id}；确认前仍由原保管人负责。</p>`:''}${btn('history','历史',u.id)}</article>`).join('')+'</div>'+
        `<div class="asset-pager">${page>1?btn('prev','上一页'):''}<span>第 ${page} 页 · ${data.total} 件</span>${page*20<data.total?btn('next','下一页'):''}</div>`;
      $('asset-unit-actions').innerHTML=Object.entries(actions).filter(([a])=>data.can_manage||!['repair','healthy','retire','lost','recover','correct'].includes(a)).map(([a,l])=>btn(a,l)).join('')+btn('clear','清空选择')+btn('print','打印标签');
      selectedSummary();status('扫码只选择单件，执行操作前须核对并确认。');
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
      (item.tracking==='legacy'?btn('migrate-start','冻结并开始核对'):btn('migrate-add','登记核对实物')+btn('migrate-finish','确认全部对应并切换')+btn('migrate-cancel','取消核对并解冻'));
    status(item.tracking==='migrating'?'型号已冻结；误录可取消本次核对后重新登记。':'尚未分配任何单件身份。');
  }
  async function act(action,id,historyPage=1){
    if(action==='prev'||action==='next'){page+=action==='next'?1:-1;return refresh();}
    if(action==='clear'){selected.clear();return refresh();}
    if(action==='history'){
      const h=await api(`units/${id}/history?page=${historyPage}`);
      $('asset-unit-history').innerHTML=`<h3>U:${id} 历史 · 第 ${h.page} 页 / 共 ${h.total} 条</h3><ol>${h.events.map(e=>`<li>#${e.id} ${esc(actions[e.action.replace('unit_','')]||e.action)} · ${esc(e.actor_name)} · ${esc(e.created_at)}<p>${esc(e.reason)}</p><details><summary>前后状态与来源</summary><pre>${esc(JSON.stringify({details:e.details,receipt:e.receipt},null,2))}</pre></details></li>`).join('')}</ol>`;
      for(const [label,p] of [['上一页',h.page-1],['下一页',h.page+1]])if(p>0&&(p-1)*100<h.total){const b=document.createElement('button');b.className='btn';b.textContent=label;b.onclick=()=>act('history',id,p).catch(e=>status(e.message));$('asset-unit-history').append(b);}return;
    }
    if(action==='print'){if(!selected.size)throw Error('请先选择需要打印的实物');return window.AssetLabels?.open([...selected.values()]);}
    if(action.startsWith('migrate-')){
      const mode=action.slice(8);let fields='';
      if(mode==='add'){
        const d=await api('units/migration/'+item.id);
        fields=campus()+select('loan_id','归属（必须与实物核对一致）',[{id:0,name:'仓库在库'},...d.loans.map(l=>({id:l.id,name:`借单 #${l.id} ${l.member_name} 未还 ${l.outstanding}`}))],0,'')+input('quantity','核对件数',1,'type="number" min="1" max="100" required')+'<label>厂家 SN（选填，一件一行）<textarea name="serials" rows="3"></textarea></label>';
      }
      core.open('旧库存迁移 · '+mode,fields+reason(true),'units/migrate',{item_id:item.id,action:mode,version:item.version},['campus_id','quantity','loan_id']);return;
    }
    if(action==='receive'){
      core.open('非耗材批量建档',campus()+input('quantity','实收件数',1,'type="number" min="1" max="100" required')+'<label>厂家 SN（选填，一件一行，行数须等于件数）<textarea name="serials" rows="3"></textarea></label>'+reason(true),'units/receive',{item_id:item.id},['quantity','campus_id']);return;
    }
    if(!selected.size)throw Error('请先选择单件；跨页选择会保留');
    let fields=`<p>本批 ${selected.size} 件：${[...selected.keys()].map(v=>'U:'+v).join('、')}。整批成功或失败；不会自动移除失败选择。</p>`;
    if(['borrow','handoff','correct'].includes(action)){
      if(action!=='borrow'||core.catalog.can_manage)fields+=select('member_id',action==='handoff'?'接收人（确认后才转移责任）':'实际保管人',action==='correct'?[{id:0,name:'仓库，无个人保管人'},...meta.members]:meta.members,action==='borrow'?core.catalog.member_id:'');
    }
    if(['return','move'].includes(action))fields+=campus();
    if(action==='return')fields+=select('health','归还状况',[{id:'normal',name:'正常'},{id:'inspection',name:'损坏／待检查'}],'normal','');
    if(['return','repair','lost','retire'].includes(action)&&[...selected.values()].some(u=>u.object_id))fields+=select('detached','确认已经拆下',[{id:'true',name:'已拆下，记录拆卸后继续'}]);
    if(action==='install')fields+=select('object_id','机器人／测试平台',meta.objects.filter(o=>o.active))+input('position','安装位置','','maxlength="120"');
    if(['move','detach','install'].includes(action))fields+=input('location','实际存放位置','','required maxlength="120"');
    if(action==='retire')fields+=select('destroyed','是否已实际销毁',[{id:'false',name:'未销毁（误报可恢复）'},{id:'true',name:'已销毁（不可恢复）'}],'false','');
    if(action==='recover')fields+='<p>恢复原身份并进入待检查，不重开旧借单。</p>';
    core.open(actions[action],fields+reason(true),'units/operate',{action,units:[...selected.values()].map(u=>({id:u.id,version:u.version}))},['member_id','campus_id','object_id']);
  }
  drawer.addEventListener('click',e=>{const b=e.target.closest('[data-unit-action]');if(b)act(b.dataset.unitAction,Number(b.dataset.id)).catch(e=>status(e.message));});
  drawer.addEventListener('change',e=>{if(e.target.dataset.unitSelect){const u=rows.find(v=>v.id===Number(e.target.dataset.unitSelect));if(e.target.checked)selected.set(u.id,u);else selected.delete(u.id);selectedSummary();}});
  $('asset-unit-close').onclick=()=>{drawer.close();opener?.focus();};
  $('asset-unit-campus').onchange=()=>{page=1;refresh();};
  $('asset-unit-mine').onclick=()=>show(null,null,true);
  document.addEventListener('click',e=>{const a=e.target.closest('[data-unit-model]');if(a){e.preventDefault();show(Number(a.dataset.unitModel));}});
  document.addEventListener('assets-loaded',()=>{if(drawer.open)refresh();});
  document.addEventListener('asset-operation-saved',e=>{if(e.detail.path==='units/operate'){selected.clear();selectedSummary();refresh();}});
  window.AssetUnits={show,refresh,select:async unit=>{if(selected.has(unit.id))throw Error('此单件已在待提交清单中');selected.set(unit.id,unit);selectedSummary();},get selection(){return [...selected.values()];}};
})();
