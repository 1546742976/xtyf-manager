/* BOM workspace: import -> column mapping -> editable rows -> inventory match -> operation. */
(() => {
  'use strict';
  const core=window.AssetLedger, $=id=>document.getElementById(id);
  const page=$('asset-bom-page');
  if(!page||!core)return;
  const file=$('asset-bom-file'), importBtn=$('asset-bom-import');
  const editor=$('asset-bom-editor'), operation=$('asset-bom-operation');
  const mappingHost=$('asset-bom-mapping'), rowHost=$('asset-bom-rows');
  const headerSelect=$('asset-bom-header-row'), campus=$('asset-bom-campus'), mode=$('asset-bom-mode');
  const sets=$('asset-bom-sets'), setsLabel=$('asset-bom-sets-label');
  const save=$('asset-bom-save'), match=$('asset-bom-match');
  const reason=$('asset-bom-reason'), status=$('asset-bom-message'), summary=$('asset-bom-summary');
  const fields=['code','name','specification','quantity','unit'];
  const labels={code:'物料编号 / C 编号',name:'物料名称',specification:'规格 / 型号',quantity:'每套或实收数量',unit:'计量单位'};
  const aliases={
    code:['编号','物料编号','物料编码','嘉立创编号','嘉立创料号','c编号','商品编号','物料代码','库存编号','sku','code','lcsc','lcscpart','partnumber','partno'],
    name:['名称','物料名称','元件名称','品名','商品名称','器件名称','name','description','component'],
    specification:['规格','规格型号','型号','封装','规格参数','spec','specification','model','footprint','package'],
    quantity:['数量','用量','每套数量','每套用量','单套用量','需求数量','实收数量','qty','quantity','count','designatorcount'],
    unit:['单位','计量单位','unit']
  };
  const normalize=v=>String(v||'').trim().toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g,'');
  const aliasLookup={};
  for(const [field,values] of Object.entries(aliases))for(const v of values)aliasLookup[normalize(v)]=field;
  const newToken=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
  let raw=[],headerAt=1,map={},rows=[],matched=false,managing=false,busy=false,pendingBatch=null;
  let importedFile='';
  const show=(value,error=false)=>{status.textContent=value;status.dataset.state=error?'error':'info';};
  const selectedRow=r=>r.candidates?.find(item=>String(item.id)===String(r.selected));
  const qty=r=>Number(r.quantity)*(mode.value==='outbound'?Number(sets.value):1);
  function reset(){
    raw=[];rows=[];map={};matched=false;pendingBatch=null;
    editor.hidden=true;operation.hidden=true;rowHost.replaceChildren();
    mappingHost.replaceChildren();summary.textContent='';save.disabled=true;
    file.value='';$('asset-bom-import-name').textContent='';
    show('请选择表格，上传后可映射列名并编辑每一行。');
  }
  function freeze(value){
    for(const element of page.querySelectorAll('input,textarea,select,button')){
      if(element===save)continue;
      element.disabled=value;
    }
  }
  function setBusy(value){busy=value;importBtn.disabled=value;match.disabled=value;save.disabled=value||!matched;}
  function configure(){
    const c=core.catalog;if(!c)return;
    managing=!!c.can_manage;
    const old=campus.value;
    campus.replaceChildren(new Option('请选择校区',''));
    for(const x of c.campuses.filter(x=>x.active))campus.add(new Option(x.name,String(x.id)));
    if([...campus.options].some(x=>x.value===old))campus.value=old;
    else if(campus.options.length===2)campus.selectedIndex=1;
    updateSummary();
  }
  function header(){return raw.find(r=>r.row_no===headerAt)?.cells||[];}
  function mapCacheKey(){return 'xtyf.assets.bom.mapping.v1:'+header().map(normalize).join('|');}
  function readMapping(){
    const heads=header(),used=new Set(),guessed={};
    heads.forEach((h,i)=>{
      const field=aliasLookup[normalize(h)];
      if(field&&guessed[field]===undefined&&!used.has(i)){guessed[field]=i;used.add(i);}
    });
    try{
      const saved=JSON.parse(localStorage.getItem(mapCacheKey())||'null');
      if(saved&&typeof saved==='object')for(const field of fields){
        if(Number.isInteger(saved[field])&&saved[field]>=0&&saved[field]<heads.length)guessed[field]=saved[field];
      }
    }catch(_){/* Some browsers disable storage. */}
    map=guessed;
  }
  function drawMapping(){
    mappingHost.replaceChildren();
    const heads=header();
    for(const field of fields){
      const label=document.createElement('label');label.textContent=labels[field]+(field==='quantity'?'（必填）':'');
      const select=document.createElement('select');
      select.setAttribute('aria-label',labels[field]+'对应表格列');
      select.add(new Option('不导入这一字段',''));
      heads.forEach((h,i)=>select.add(new Option((i+1)+' · '+(h||'未命名列'),String(i))));
      select.value=map[field]===undefined?'':String(map[field]);
      select.addEventListener('change',()=>{
        if(select.value==='')delete map[field];else map[field]=Number(select.value);
        try{localStorage.setItem(mapCacheKey(),JSON.stringify(map));}catch(_){}
        applyMapping();
      });
      label.append(select);mappingHost.append(label);
    }
  }
  function clearMatch(){
    matched=false;
    for(const r of rows){r.candidates=[];r.selected='';}
    save.disabled=true;
  }
  function applyMapping(){
    const data=raw.filter(record=>record.row_no>headerAt&&record.cells.some(cell=>cell));
    if(data.length>200){show('表头以下超过 200 行，请拆分文件。',true);rowHost.replaceChildren();rows=[];clearMatch();return;}
    rows=data.map(record=>{
      const get=key=>{
        const index=map[key];
        return index===undefined?'':String(record.cells[index]||'').trim();
      };
      return {row_no:record.row_no,code:get('code'),name:get('name'),specification:get('specification'),
        quantity:get('quantity'),unit:get('unit')||'个',candidates:[],selected:''};
    });
    clearMatch();renderRows();show('已按列映射生成 '+rows.length+' 行。可直接编辑，完成后选择操作并匹配库存。');
  }
  function renderHeaders(){
    headerSelect.replaceChildren();
    for(const record of raw.filter(x=>x.row_no<=10)){
      headerSelect.add(new Option('第 '+record.row_no+' 行 · '+record.cells.slice(0,4).join(' / ').slice(0,76),String(record.row_no)));
    }
    headerSelect.value=String(headerAt);
    readMapping();drawMapping();applyMapping();
  }
  async function importFile(){
    if(busy||pendingBatch||core.blocked){show('存在未确认的操作，请先核对。',true);return;}
    const input=file.files[0];
    if(!input){show('请先选择 CSV、TSV 或 XLSX 文件。',true);return;}
    if(input.size>2*1024*1024){show('文件超过 2 MiB，请拆分。',true);return;}
    setBusy(true);show('正在读取表格列与内容…');
    const data=new FormData();data.append('file',input);
    try{
      const response=await fetch('/api/assets/bom/import',{method:'POST',body:data});
      const result=await response.json();
      if(!response.ok||!result.ok)throw Error(result.error||'表格导入失败');
      raw=result.lines;headerAt=result.header_row;importedFile=result.file_name;
      editor.hidden=false;operation.hidden=false;
      $('asset-bom-import-name').textContent=importedFile+' · '+raw.length+' 行（含表头）';
      renderHeaders();
    }catch(error){show(error.message,true);}
    finally{setBusy(false);updateSummary();}
  }
  const cellInput=(r,field,kind='text')=>{
    const input=document.createElement('input');input.type=kind;input.value=r[field]??'';
    input.setAttribute('aria-label','表格第 '+r.row_no+' 行 '+labels[field]);
    input.maxLength=field==='unit'?12:field==='quantity'?40:120;
    if(kind==='number'){input.min=1;input.max=1000000000;input.step=1;}
    input.addEventListener('input',()=>{
      r[field]=input.value;clearMatch();updateSummary();
      show('内容已修改。需要重新匹配库存后才能提交。');
    });
    return input;
  };
  function attach(td,control){td.append(control);return td;}
  const td=()=>document.createElement('td');
  function renderRows(){
    rowHost.replaceChildren();
    const table=document.createElement('table');table.className='asset-bom-table asset-bom-editor-table';
    const thead=document.createElement('thead');
    const head=document.createElement('tr');
    for(const label of ['原表行','编号','名称','规格型号','数量','单位','库存匹配 / 新建','库存变化','操作']){
      const th=document.createElement('th');th.textContent=label;head.append(th);
    }
    thead.append(head);table.append(thead);
    const tbody=document.createElement('tbody');
    for(const r of rows){
      const tr=document.createElement('tr');
      tr.dataset.row=String(r.row_no);
      const no=td();no.textContent=String(r.row_no);tr.append(no);
      for(const field of fields)tr.append(attach(td(),cellInput(r,field,field==='quantity'?'number':'text')));
      const choose=td(),picker=document.createElement('select');
      picker.setAttribute('aria-label','第 '+r.row_no+' 行库存匹配');
      picker.add(new Option('请选择库存物料',''));
      for(const item of (r.candidates||[])){
        picker.add(new Option('M:'+item.id+' · '+item.name+(item.specification?' / '+item.specification:''),String(item.id)));
      }
      if(managing&&mode.value==='inbound'&&r.name.trim())picker.add(new Option('＋ 新建数量耗材','new'));
      picker.value=r.selected||'';
      picker.addEventListener('change',()=>{r.selected=picker.value;renderRows();});
      choose.append(picker);
      const manual=document.createElement('div');manual.className='asset-bom-manual';
      const idInput=document.createElement('input');idInput.type='number';idInput.min='1';idInput.placeholder='已有物料 ID';
      idInput.setAttribute('aria-label','第 '+r.row_no+' 行指定物料 ID');
      const lookup=document.createElement('button');lookup.type='button';lookup.className='btn';lookup.textContent='指定';
      lookup.disabled=!matched;
      lookup.addEventListener('click',async()=>{
        const id=Number(idInput.value);
        if(!Number.isSafeInteger(id)||id<1){show('请输入 M: 后的数字 ID。',true);return;}
        lookup.disabled=true;
        try{
          const data=await core.api('items/'+id);
          const found=data.campuses.find(c=>c.id===Number(campus.value));
          const candidate={...data.item,available:found?.available||0,stock_version:found?.stock_version||0};
          if(!r.candidates.some(c=>c.id===id))r.candidates.push(candidate);
          r.selected=String(id);renderRows();
        }catch(error){show(error.message,true);lookup.disabled=false;}
      });
      manual.append(idInput,lookup);choose.append(manual);tr.append(choose);
      const stock=td(),item=selectedRow(r),amount=qty(r);
      stock.textContent=r.selected==='new'?'新建后入库 '+r.quantity:
        item&&Number.isSafeInteger(amount)?'现有 '+item.available+' → '+(item.available+(mode.value==='inbound'?amount:-amount)):
        matched?'未指定库存物料':'待匹配';
      tr.append(stock);
      const actions=td(),remove=document.createElement('button');
      remove.className='btn';remove.type='button';remove.textContent='移除行';
      remove.addEventListener('click',()=>{
        rows=rows.filter(x=>x!==r);clearMatch();renderRows();
        show('已移除该行，重新匹配后可提交。');
      });
      actions.append(remove);tr.append(actions);tbody.append(tr);
    }
    table.append(tbody);rowHost.append(table);updateSummary();
  }
  function validate(){
    const errors=[],totals=new Map();let total=0;
    if(!rows.length)errors.push('没有可操作的物料行');
    if(!campus.value)errors.push('未选择操作校区');
    const kits=Number(sets.value);
    if(mode.value==='outbound'&&(!Number.isSafeInteger(kits)||kits<1||kits>1000000))errors.push('套数必须为 1 至 1000000');
    for(const r of rows){
      const n=Number(r.quantity),count=qty(r);
      if(!Number.isSafeInteger(n)||n<1||n>1000000000||!Number.isSafeInteger(count)||count<1||count>1000000000)
        errors.push('第 '+r.row_no+' 行数量无效或超限');
      if(!r.name.trim()&&!r.code.trim())errors.push('第 '+r.row_no+' 行缺少编号与名称');
      if(fields.some(f=>String(r[f]||'').length>(f==='unit'?12:f==='quantity'?40:120)))errors.push('第 '+r.row_no+' 行字段过长');
      if(matched){
        const item=selectedRow(r);
        if(r.selected==='new'){
          if(!managing||mode.value!=='inbound'||!r.name.trim()||r.code&&!/^C[1-9][0-9]{0,17}$/i.test(r.code.trim()))
            errors.push('第 '+r.row_no+' 行不能创建该物料');
        }else if(!item)errors.push('第 '+r.row_no+' 行未匹配物料');
        else if(!item.active||['unit','migrating','status'].includes(item.tracking))
          errors.push('M:'+item.id+' 不能按数量出入库');
        else{
          const aggregate=totals.get(item.id)||{qty:0,available:item.available,name:item.name};
          aggregate.qty+=count;totals.set(item.id,aggregate);
        }
      }
      if(Number.isFinite(count))total+=count;
    }
    if(mode.value==='outbound')for(const [id,value] of totals){
      if(value.qty>value.available)errors.push('M:'+id+' 库存不足：需要 '+value.qty+'，在库 '+value.available);
      if(value.qty>1000000000)errors.push('M:'+id+' 汇总数量超限');
    }
    if(!reason.value.trim())errors.push('用途 / 原因未填写');
    return {errors,total,totals};
  }
  function updateSummary(){
    const data=validate();
    summary.textContent=rows.length?'当前 '+rows.length+' 行 · '+(mode.value==='outbound'?'按 '+sets.value+' 套共 ':'入库共 ')+data.total+
      ' 件'+(matched?' · 关联 '+data.totals.size+' 种库存物料':' · 尚未匹配库存'):'尚未导入有效物料行';
    save.disabled=busy||!!pendingBatch||!matched||!!data.errors.length||core.blocked;
    if(matched&&data.errors.length)show('提交受阻：'+data.errors.slice(0,3).join('；'),true);
  }
  async function matchRows(){
    if(busy||pendingBatch||core.blocked)return;
    matched=false;
    const {errors}=validate();
    const matchingErrors=errors.filter(e=>!e.includes('用途 / 原因'));
    if(matchingErrors.length){
      show('请检查：'+matchingErrors.slice(0,4).join('；')+'。',true);
      matched=false;updateSummary();return;
    }
    setBusy(true);show('正在查询库存物料及当前库存版本…');
    try{
      const body={campus_id:Number(campus.value),rows:rows.map(r=>({
        code:r.code,name:r.name,specification:r.specification,quantity:Number(r.quantity),unit:r.unit
      }))};
      const response=await fetch('/api/assets/bom/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();
      if(!response.ok||!result.ok)throw Error(result.error||'匹配库存失败');
      if(result.matches.length!==rows.length)throw Error('服务器匹配结果行数异常');
      managing=!!result.can_manage;
      rows.forEach((r,i)=>{
        r.candidates=result.matches[i];
        r.selected=r.candidates.length===1?String(r.candidates[0].id):'';
      });
      matched=true;renderRows();show('已匹配库存。请逐行选择正确物料，再填写用途并确认提交。');
    }catch(error){show(error.message,true);}
    finally{busy=false;updateSummary();}
  }
  function writeRows(){
    return rows.map(r=>{
      const item=selectedRow(r);
      return r.selected==='new'?{item_id:null,quantity:Number(r.quantity),
        code:r.code.trim(),name:r.name.trim(),specification:r.specification.trim(),unit:r.unit.trim()}:
        {item_id:item.id,quantity:Number(r.quantity),version:item.version,stock_version:item.stock_version};
    });
  }
  async function submit(event){
    event.preventDefault();
    if(busy||core.writing||(core.blocked&&!pendingBatch))return;
    if(pendingBatch&&(pendingBatch.actor_id!==core.catalog?.member_id||core.saved?.request_id!==pendingBatch.request_id)){
      show('请使用原账号核对待确认请求，不能以当前身份重新登记。',true);return;
    }
    if(!pendingBatch){
      if(!matched){show('请先完成库存匹配。',true);return;}
      const {errors}=validate();
      if(errors.length){show('无法提交：'+errors.join('；'),true);return;}
      const actor=core.catalog?.member_id;
      if(!actor||core.blocked){show('存在未确认的操作，请先恢复核对。',true);return;}
      pendingBatch={
        actor_id:actor,path:'bom/commit',request_id:newToken(),
        body:{mode:mode.value,campus_id:Number(campus.value),sets:mode.value==='outbound'?Number(sets.value):1,
          reason:reason.value.trim(),rows:writeRows()}
      };
      try{core.rememberPending(pendingBatch);}
      catch(_){pendingBatch=null;show('无法保存请求重试信息，本次没有提交。',true);return;}
    }
    busy=true;core.setWriting(true);freeze(true);save.disabled=true;show('正在批量写入库存与流水…');
    try{
      const result=await core.api(pendingBatch.path,{...pendingBatch.body,request_id:pendingBatch.request_id});
      core.forgetPending();pendingBatch=null;
      busy=false;freeze(false);reset();
      await core.load();show('批量操作成功：已登记 '+result.operations.length+' 种物料，请到物料仓库查看记录。');
    }catch(error){
      if(error.businessRejected){
        core.forgetPending();pendingBatch=null;
        busy=false;freeze(false);clearMatch();renderRows();
        show(error.message+'；未写入任何物料。请重新匹配库存后再提交。',true);
      }else{
        busy=false;save.disabled=false;
        show('提交结果暂未确认：'+error.message+'。仅可通过下方按钮使用同一个请求标识重试，不要重新建批次。',true);
      }
    }finally{
      busy=false;
      core.setWriting(false);
      if(pendingBatch)save.disabled=false;
    }
  }
  importBtn.addEventListener('click',importFile);
  $('asset-bom-clear').addEventListener('click',()=>{
    if(busy||pendingBatch)return;
    reset();
  });
  headerSelect.addEventListener('change',()=>{
    headerAt=Number(headerSelect.value);
    readMapping();drawMapping();applyMapping();
  });
  $('asset-bom-add-row').addEventListener('click',()=>{
    if(rows.length>=200){show('最多 200 行。',true);return;}
    rows.push({row_no:Math.max(0,...rows.map(r=>r.row_no))+1,code:'',name:'',specification:'',quantity:'1',unit:'个',candidates:[],selected:''});
    clearMatch();renderRows();
  });
  campus.addEventListener('change',()=>{clearMatch();renderRows();show('校区已变更，请重新匹配库存。');});
  mode.addEventListener('change',()=>{
    setsLabel.hidden=mode.value!=='outbound';
    if(mode.value==='outbound')for(const r of rows)if(r.selected==='new')r.selected='';
    renderRows();
  });
  sets.addEventListener('input',()=>{renderRows();});
  reason.addEventListener('input',updateSummary);
  match.addEventListener('click',matchRows);
  $('asset-bom-form').addEventListener('submit',submit);
  document.addEventListener('assets-loaded',configure);
  document.addEventListener('asset-pending-cleared',event=>{
    if(pendingBatch?.request_id===event.detail?.request_id){pendingBatch=null;freeze(false);clearMatch();updateSummary();}
  });
  if(core.catalog)configure();
})();
