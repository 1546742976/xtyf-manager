/* Decoding fills a preview. Only an explicit, idempotent submission changes stock. */
(() => {
  'use strict';
  const core=window.AssetLedger,$=id=>document.getElementById(id),dialog=$('asset-scan-dialog');
  const {esc,input,reason,options}=core;let resolved=null,sequence=0,opener;
  const status=message=>$('asset-scan-status').textContent=message;
  const packages=new Map();
  function queue(){
    $('asset-scan-queue').innerHTML=`<p>待登记包装 ${packages.size} 个 · 已选单件 ${window.AssetUnits.selection.length} 件</p><button class="btn" id="scan-queue-submit">核对包装批量入库</button><button class="btn" id="scan-queue-units">打开单件操作</button><button class="btn" id="scan-queue-clear">清空包装清单</button>`;
    $('scan-queue-units').onclick=()=>{close();window.AssetUnits.show();};
    $('scan-queue-clear').onclick=()=>{packages.clear();queue();};
    $('scan-queue-submit').onclick=()=>{
      if(!packages.size){status('包装清单为空');return;}
      const values=[...packages.values()];
      const fields=values.map((v,i)=>`<fieldset><legend>${esc(v.item.name)} · 标签数量 ${v.parsed.label_quantity??'未知'}</legend>`+input('q'+i,'实收数量',v.parsed.label_quantity??'','type="number" min="1" step="1" required')+`<label>校区<select name="c${i}" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label>`+(v.history.length?`<label>重复登记核对原因<textarea name="d${i}" required maxlength="500"></textarea></label>`:'')+'</fieldset>').join('');
      close();core.open('核对整批包装',fields+reason(false),'scan/batch',{},[],form=>({receipts:values.map((v,i)=>({raw:v.parsed.raw,item_id:v.item.id,quantity:Number(form.get('q'+i)),campus_id:Number(form.get('c'+i)),receipt_version:v.receipt_version,duplicate_reason:form.get('d'+i)||'',reason:form.get('reason')}))}));
    };
  }
  function close(){dialog.close();opener?.focus();}
  $('asset-scan').onclick=()=>{opener=document.activeElement;dialog.showModal();queue();if(!matchMedia('(pointer:coarse)').matches)$('asset-scan-raw').focus();};
  $('asset-scan-close').onclick=close;
  async function resolve(raw){
    const own=++sequence;resolved=null;$('asset-scan-preview').replaceChildren();status('正在识别…');
    try{
      const value=await core.api('scan/resolve',{raw});if(own!==sequence)return;
      resolved=value;const p=value.parsed;
      const mode=$('asset-scan-mode').value;
      if(mode==='units'){
        if(p.type!=='unit')throw Error('请扫描具体设备标签；物料编号不能替代单件身份');
        await window.AssetUnits.select(value.unit);queue();status('单件已加入清单，尚未执行操作。');$('asset-scan-raw').value='';return;
      }
      if(mode==='receipts'){
        if(p.type!=='package')throw Error('请扫描采购包装标签');
        if(value.item){
          if(packages.has(p.raw))throw Error('本清单已包含同一标签，不自动累计数量');
          if(packages.size>=20)throw Error('每批最多 20 个包装');
          packages.set(p.raw,value);queue();status(value.history.length?'此包装曾登记，提交前需明确填写重复收货原因':'已加入包装清单；确认前不入库');$('asset-scan-raw').value='';return;
        }
      }
      if(p.type==='unit'){
        status('已找到单件；识别不会改变库存。');
        $('asset-scan-preview').innerHTML=`<p>${esc(value.item.name)} · 单件 #${value.unit.id}</p><button class="btn" id="scan-unit-open">打开单件抽屉</button>`;
        $('scan-unit-open').onclick=()=>{close();window.AssetUnits?.show(value.item.id,value.unit.id);};return;
      }
      status(p.quantity_error||'已识别，请核对实际数量和校区后登记。');
      $('asset-scan-preview').innerHTML=`<p>商品：${esc(p.code||'内部物料')} · 型号：${esc(p.model||value.item?.specification||'')}</p>`+
        `<p>标签数量：${p.label_quantity??'未提供'} · 订单：${esc(p.order||'未提供')}</p>`+
        '<p>国内商品自动查询尚未配置，可使用标签资料和手工补录。</p>'+
        (value.history.length?`<p role="alert">此标签有 ${value.history.length} 条近期登记，请核对，不能重复增加库存。</p><ul>${value.history.map(h=>`<li>#${h.id} ${esc(h.created_at)} · ${esc(h.actor_name)} · 实收 ${h.actual_quantity}</li>`).join('')}</ul>`:'')+
        (value.item?`<p>本地物料：${esc(value.item.name)}</p><button class="btn primary" id="scan-receive">${p.type==='package'?'核对实收并登记':'打开物料'}</button>`:
          `<p>尚未匹配本地物料。</p>${value.can_manage?'<button class="btn" id="scan-create">确认建档</button><button class="btn" id="scan-bind">关联已有物料</button>':'<p>请联系管理人员关联或建档。</p>'}`);
      if($('scan-receive'))$('scan-receive').onclick=()=>{
        close();if(p.type!=='package'){location.href='/assets/detail?id='+value.item.id;return;}
        core.open('包装入库预览',`<p>${esc(value.item.name)} · 标签数量 ${p.label_quantity??'未知'}</p>`+
          input('quantity','实收数量',p.label_quantity??'','type="number" min="1" step="1" required')+
          `<label>校区<select name="campus_id" required>${options(core.catalog.campuses.filter(c=>c.active),'','请选择')}</select></label>`+
          (value.history.length?'<label>重复标签核对说明（必填）<textarea name="duplicate_reason" required maxlength="500"></textarea></label>':'')+reason(false),
          'scan/receive',{raw:p.raw,item_id:value.item.id,receipt_version:value.receipt_version},['quantity','campus_id']);
      };
      if($('scan-create'))$('scan-create').onclick=()=>{
        close();core.open('确认物料资料',input('name','名称','','required maxlength="120"')+
          input('specification','厂家型号',p.model||'','maxlength="120"')+input('supplier_code','商品编号',p.code,'required readonly')+
          input('unit','单位','颗','required maxlength="12"')+'<label>管理类型<select name="kind"><option value="consumable">耗材</option><option value="returnable">非耗材（逐件建档）</option></select></label>',
          'catalog/items',{});
      };
      if($('scan-bind'))$('scan-bind').onclick=async()=>{
        const found=await core.api('items?q='+encodeURIComponent(p.model||p.code));
        close();core.open('关联已有型号','<p>仅显示名称／规格匹配项；如无匹配，可在物料编辑页关联供应商编号。</p>'+`<label>物料<select name="item_id" required>${options(found.items,'','请选择')}</select></label>`+reason(true),'identifiers',{code:p.code},['item_id']);
      };
    }catch(e){if(own===sequence)status(e.message);}
  }
  $('asset-scan-form').onsubmit=e=>{e.preventDefault();resolve($('asset-scan-raw').value);};
  $('asset-scan-raw').onkeydown=e=>{if(e.key===$('asset-scan-end').value&&!e.shiftKey&&!e.isComposing){e.preventDefault();resolve(e.target.value);}};
  document.addEventListener('asset-operation-saved',e=>{if(e.detail.path==='scan/batch'){packages.clear();queue();}});
  window.AssetScan={resolve,dialog,get resolved(){return resolved;}};
})();
