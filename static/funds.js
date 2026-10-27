
function escText(value) {
  return BusinessUI.escape(value);
}
function fundsToast(message, kind) {
  var box = document.getElementById('funds-feedback');
  if (!box) { alert(message); return; }
  box.textContent = message;
  box.className = 'ux-toast ' + (kind || '');
  clearTimeout(window.__fundsToastTimeout);
  if(kind!=='error')window.__fundsToastTimeout = setTimeout(function() { box.hidden = true; }, 4200);
  box.setAttribute('role',kind==='error'?'alert':'status');
  box.hidden = false;
}
var API = '/funds/api/records', sort = {by:'record_date',order:'DESC'}, edM, ct=null;
var chartRequest = 0, chartAbort = null, chartPalette = '', chartDirty = false;
var ME_NAME = window.XTYF_USER.name, ME_USERNAME = window.XTYF_USER.username;
var CAN_REVIEW = window.XTYF_USER.canReview;
var fundsCreate = null, fundsCreateBusy = false, fundsFormRequest = null;
var recordsRequest = 0, recordsAbort = null, recordsInactive = false;
window.addEventListener('pagehide', function(){
  recordsInactive = true; recordsRequest++;
  if(recordsAbort)recordsAbort.abort();
});
window.addEventListener('pageshow', function(event){
  if(event.persisted && recordsInactive){recordsInactive=false;reload();}
});

document.addEventListener('DOMContentLoaded', function(){
  edM = new bootstrap.Modal('#editMd');
  document.getElementById('aDt').value = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  document.getElementById('aApplicant').value = ME_NAME;
  if (CAN_REVIEW) { loadPending(); setInterval(loadPending, 30000); }
  reload(); loadStats();
  initializeFundsCreate();
  document.getElementById('mainTab').addEventListener('shown.bs.tab', function(e){
    if (e.target.dataset.bsTarget === '#t-charts' && (!ct || chartDirty)) {
      loadChart(ct || 'monthly', document.querySelector('#t-charts .btn-group .active'));
      loadStats();
    }
  });
});

function renderFundsCreate(message) {
  var entries = fundsCreate.pending();
  document.getElementById('funds-create-fields').disabled = fundsCreateBusy || entries.length > 0;
  var retry = document.getElementById('funds-create-retry');
  retry.hidden = !entries.length; retry.disabled = fundsCreateBusy;
  document.getElementById('funds-create-status').textContent = message || (entries.length
    ? '结果待确认：' + entries.length + ' 笔原请求已保留，已暂停新增。当前请求：' + entries[0].body.record_type + ' ' + entries[0].body.amount + ' 元，请核对后重试原请求。'
    : '新增就绪。提交结果不明时，可刷新后重试原请求。');
}

async function initializeFundsCreate() {
  try {
    fundsCreate = FundsCreate.create({storage:localStorage, fetch:BusinessUI.fetch,
      server:()=>window.XTYF ? (parent.XTYF || window.XTYF).config.serverUrl : location.origin,
      uuid:()=>Array.from(crypto.getRandomValues(new Uint8Array(16)), x=>x.toString(16).padStart(2,'0')).join('')});
    var entries = await fundsCreate.connect();
    if (entries.length) {
      var body = entries[0].body;
      var fields = {aT:'record_type',aAmt:'amount',aCat:'category',aSD:'source_dest',aDt:'record_date',
        aNotes:'notes',aApplicant:'applicant',aInvCnt:'invoice_count',aHandler:'handler'};
      Object.keys(fields).forEach(id=>{document.getElementById(id).value=body[fields[id]] ?? '';});
      toggleF(); fundsFormRequest = body.request_id;
    }
    renderFundsCreate();
    document.getElementById('funds-create-retry').onclick = ()=>submitFundsCreate(fundsCreate.pending()[0]);
    window.addEventListener('storage',()=>{try{renderFundsCreate();}catch(error){fundsToast(error.message,'error');}});
  } catch(error) {
    document.getElementById('funds-create-fields').disabled = true;
    document.getElementById('funds-create-status').textContent = '已暂停新增：' + error.message;
  }
}

async function submitFundsCreate(entry) {
  if (fundsCreateBusy || !entry) return;
  fundsCreateBusy = true;
  try {
    renderFundsCreate('正在核对原请求并提交…');
    var result = await fundsCreate.send(entry);
    if (result.state === 'confirmed') {
      var j = result.receipt;
      var message = j.ledger_sync === 'failed' ? j.msg : '该创建请求已处理（记录 ' + j.id + '）。最新状态已重新读取。';
      if (fundsFormRequest === entry.body.request_id) { clearF(); fundsFormRequest = null; }
      reload(); loadStats(); if(CAN_REVIEW)loadPending();
      fundsToast(message, j.ledger_sync === 'failed' ? 'error' : 'success');
      renderFundsCreate(message);
    } else if (result.state === 'rejected') {
      renderFundsCreate(result.receipt.msg); fundsToast(result.receipt.msg,'error');
    } else { renderFundsCreate(result.message); fundsToast(result.message,'error'); }
  } catch(error) {
    document.getElementById('funds-create-status').textContent = '请求已保留，请刷新核对：' + error.message;
  } finally {
    fundsCreateBusy = false;
    try { renderFundsCreate(document.getElementById('funds-create-status').textContent); }
    catch(error) { document.getElementById('funds-create-fields').disabled = true; }
  }
}

document.addEventListener('workspace-theme-change', function(){
  if (!ct || chartPalette === document.documentElement.dataset.palette) return;
  chartDirty = true;
  if (document.getElementById('t-charts')?.classList.contains('active')) {
    loadChart(ct, document.querySelector('#t-charts .btn-group .active'));
  } else {
    // An old hidden request must not paint an outdated palette when it completes.
    chartRequest += 1;
    chartAbort?.abort();
  }
});

function toggleF(){
  var t = document.getElementById('aT').value, isExp = (t==='出');
  document.getElementById('expF').style.display = isExp?'block':'none';
  document.getElementById('lSD').textContent = isExp?'去向':(t==='进'?'来源':'来源 / 去向');
  document.getElementById('aSD').placeholder = isExp?'例如：淘宝采购电机':(t==='进'?'例如：学校拨款':'');
  document.getElementById('lSD').className = isExp?'form-label required':'form-label';
  document.getElementById('aSD').required = isExp;
  document.getElementById('aApplicant').required = isExp;
  document.getElementById('aInvCnt').required = isExp;
  document.getElementById('aHandler').required = isExp;
  if(isExp) document.getElementById('aInvCnt').value = document.getElementById('aInvCnt').value || 1;
}
function clearF(){
  document.getElementById('addF').reset();
  document.getElementById('aDt').value = new Date().toISOString().slice(0,10);
  document.getElementById('expF').style.display = 'none';
  document.getElementById('lSD').textContent='来源 / 去向';
  document.getElementById('lSD').className = 'form-label';
  document.getElementById('aSD').required = false;
  document.getElementById('aApplicant').required = false;
  document.getElementById('aInvCnt').required = false;
  document.getElementById('aHandler').required = false;
  document.getElementById('aInvCnt').value = '1';
  document.getElementById('aApplicant').value = ME_NAME;
}
function resetF(){ document.querySelectorAll('#ft,#fkw,#famin,#famax,#fdf,#fdt').forEach(function(e){e.value=''}); reload(); }
function setSort(f){ sort.order=(sort.by===f?(sort.order==='ASC'?'DESC':'ASC'):'DESC'); sort.by=f; reload(); }

function fundsListState(state, message){
  document.getElementById('funds-list-loading').hidden = state !== 'loading';
  document.getElementById('funds-list-error').hidden = state !== 'error';
  document.getElementById('funds-list-error-message').textContent = message || '';
  document.getElementById('funds-records').setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
  if(state !== 'ready'){
    document.getElementById('tbody').innerHTML = '';
    document.getElementById('rcnt').textContent = '';
    document.getElementById('empty').style.display = 'none';
  }
}

function reload(){
  if(recordsInactive)return Promise.resolve();
  var own = ++recordsRequest;
  if(recordsAbort)recordsAbort.abort();
  var controller = new AbortController(); recordsAbort = controller;
  fundsListState('loading');
  loadLedgerStatus();
  var p = new URLSearchParams();
  [{id:'ft',key:'record_type'},{id:'fkw',key:'keyword'},{id:'famin',key:'amount_min'},{id:'famax',key:'amount_max'},{id:'fdf',key:'date_from'},{id:'fdt',key:'date_to'}].forEach(function(x){
    var v = document.getElementById(x.id).value.trim(); if(v) p.append(x.key,v);
  });
  p.append('sort_by',sort.by); p.append('sort_order',sort.order);
  return BusinessUI.fetch(API+'?'+p,{signal:controller.signal}).then(function(r){return r.json()}).then(function(j){
    if(recordsInactive || own !== recordsRequest)return;
    if (!j || j.code !== 0 || !Array.isArray(j.data)) throw new Error((j && j.msg) || '服务端响应异常');
    fundsListState('ready');
    var rows = j.data;
    document.getElementById('rcnt').textContent = rows.length + ' 条记录';
    document.getElementById('empty').style.display = rows.length?'none':'block';
    document.getElementById('tbody').innerHTML = rows.length ? rows.map(function(r){
      return '<tr>'+
        '<td>'+r.id+'</td>'+
        '<td><span class="'+(r.record_type==='进'?'badge-in':'badge-out')+'">'+(r.record_type==='进'?'进账':'支出')+'</span></td>'+
        '<td>¥'+Money.format(r.amount)+'</td>'+
        '<td>'+escText(r.category||'-')+'</td>'+
        '<td title="'+escText(r.source_dest||'')+'">'+escText(r.source_dest||'-')+'</td>'+
        '<td>'+escText(r.applicant||'-')+'</td>'+
        '<td>'+(r.invoice_count||0)+'</td>'+
        '<td>'+escText(r.handler||'-')+'</td>'+
        '<td>'+r.record_date+'</td>'+
        '<td>'+((r.review_status==='待审核')?'<span class="badge-pending">待审核</span> '+(CAN_REVIEW?'<button class="btn btn-outline-primary btn-sm" onclick="reviewRecord('+r.id+','+r.version+')">审核</button>':''):'<span class="badge-ok">已审核</span>')+'</td>'+
        '<td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="'+(r.notes||'').replace(/"/g,'&quot;')+'">'+escText(r.notes||'-')+'</td>'+
        '<td><button class="btn btn-outline-secondary btn-sm" aria-label="编辑第 '+r.id+' 条记录" title="编辑记录" onclick="edR('+r.id+')"><i class="bi bi-pencil"></i></button> <button class="btn btn-outline-danger btn-sm" aria-label="删除第 '+r.id+' 条记录" title="删除记录" onclick="delR('+r.id+','+r.version+')"><i class="bi bi-trash"></i></button></td>'+
        '</tr>';
    }).join('') : '';
  }).catch(function(e){
    if(recordsInactive || own !== recordsRequest || controller.signal.aborted)return;
    fundsListState('error','记录加载失败：'+e.message);
  }).finally(function(){if(own === recordsRequest)recordsAbort=null;});
}

async function loadLedgerStatus() {
  var status = document.getElementById('funds-ledger-state');
  if (!status) return;
  try {
    var response = await fetch('/funds/api/ledger-sync'), result = await response.json();
    if (!response.ok || result.code !== 0) throw new Error(result.msg || '无法读取台账同步状态');
    status.textContent = result.state === 'ok' ? 'TXT 台账已同步' : '数据库已保存，TXT 台账未同步；请使用“重新同步台账”或联系资金管理人员。';
    status.dataset.state = result.state;
    var button = document.getElementById('funds-ledger-repair');
    if (button) button.hidden = !result.can_sync;
  } catch (error) { status.textContent = '台账同步状态暂不可用：' + error.message; }
}

async function repairFundsLedger(button) {
  button.disabled = true;
  try {
    var response = await fetch('/funds/api/ledger-sync', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var result = await response.json();
    if (!response.ok || result.code !== 0) throw new Error(result.msg || '台账同步失败');
    fundsToast(result.state === 'ok' ? 'TXT 台账已重新同步，资金记录未改变' : '同步期间又有新记录，请再次同步核对', result.state === 'ok' ? 'success' : 'error');
  } catch (error) { fundsToast(error.message, 'error'); }
  finally { button.disabled = false; await loadLedgerStatus(); }
}

async function handleAdd(e){
  e.preventDefault();
  var d = {
    record_type: document.getElementById('aT').value,
    amount: document.getElementById('aAmt').value,
    category: document.getElementById('aCat').value,
    source_dest: document.getElementById('aSD').value.trim(),
    record_date: document.getElementById('aDt').value,
    notes: document.getElementById('aNotes').value.trim()
  };
  d.applicant = document.getElementById('aApplicant').value.trim() || ME_NAME;
  if(d.record_type==='出'){
    d.invoice_count = parseInt(document.getElementById('aInvCnt').value)||0;
    d.handler = document.getElementById('aHandler').value.trim();
  }
  try {
    if (fundsCreateBusy) return;
    var entry = fundsCreate.prepare(d);
    fundsFormRequest = entry.body.request_id;
    await submitFundsCreate(entry);
  } catch(e){ fundsToast('未发送新增请求：'+e.message,'error'); }
}

async function edR(id){
  try {
    var r = await BusinessUI.fetch(API+'/'+id), j = await r.json();
    var d = j.code===0 ? j.data : null;
    if(!d) return fundsToast(j.msg||'记录不存在','error');
    document.getElementById('eId').value=d.id; document.getElementById('eId').dataset.version=d.version; document.getElementById('eT').value=d.record_type;
    document.getElementById('eAmt').value=d.amount; document.getElementById('eCat').value=d.category||'';
    document.getElementById('eSD').value=d.source_dest||''; document.getElementById('eDt').value=d.record_date;
    document.getElementById('eInv').value=d.invoice_count||0; document.getElementById('eApl').value=d.applicant||'';
    document.getElementById('eHdl').value=d.handler||''; document.getElementById('eNts').value=d.notes||'';
    edM.show();
  } catch(e){ fundsToast('记录加载失败：'+e.message,'error'); }
}

async function saveE(){
  var d = {
    version: Number(document.getElementById('eId').dataset.version||0),
    record_type: document.getElementById('eT').value,
    amount: document.getElementById('eAmt').value,
    category: document.getElementById('eCat').value,
    source_dest: document.getElementById('eSD').value.trim(),
    record_date: document.getElementById('eDt').value,
    invoice_count: parseInt(document.getElementById('eInv').value)||0,
    applicant: document.getElementById('eApl').value.trim(),
    handler: document.getElementById('eHdl').value.trim(),
    notes: document.getElementById('eNts').value.trim()
  };
  try {
    var r = await BusinessUI.fetch(API+'/'+document.getElementById('eId').value,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    var j = await r.json();
    if(r.status===409){ fundsToast(j.msg||'记录已变更，请刷新后重试','error'); reload(); return; }
    if(j.code===0){ edM.hide(); fundsToast(j.ledger_sync==='failed'?j.msg:'记录已更新，审核状态可能需要重新确认',j.ledger_sync==='failed'?'error':'success'); reload(); loadStats(); }
    else fundsToast(j.msg || '操作失败','error');
  } catch(e){ fundsToast('保存失败：'+e.message,'error'); }
}

async function delR(id,version){
  if(!confirm('确定永久删除此资金记录？此操作不可撤销。')) return;
  try {
    var response = await BusinessUI.fetch(API+'/'+id+'?version='+encodeURIComponent(version),{method:'DELETE'});
    var result = await response.json();
    if (response.status===409){ fundsToast(result.msg||'记录已变更，请刷新后重试','error'); reload(); return; }
    if (!response.ok || result.code !== 0) throw new Error(result.msg || '服务端拒绝删除');
    fundsToast(result.ledger_sync==='failed'?result.msg:'记录已删除',result.ledger_sync==='failed'?'error':'success');
    reload(); loadStats();
  } catch(e){ fundsToast('删除失败：'+e.message,'error'); }
}

async function reviewRecord(id,version){
  if(!CAN_REVIEW || !confirm('确认审核通过该记录？')) return;
  try{
    var r = await BusinessUI.fetch(API+'/'+id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:version})});
    var j = await r.json();
    if(r.status===409){fundsToast(j.msg,'error');reload();return}
    if(j.code===0){ fundsToast(j.ledger_sync==='failed'?j.msg:'已审核通过',j.ledger_sync==='failed'?'error':'success'); reload(); loadStats(); if(CAN_REVIEW)loadPending(); }
    else fundsToast(j.msg||'操作失败','error');
  }catch(e){ fundsToast('操作失败：'+e.message,'error'); }
}

async function loadPending(){
  if (document.hidden) return;
  try{
    var r = await BusinessUI.fetch('/funds/api/pending_review');
    var j = await r.json();
    var badge = document.getElementById('fundsPendingBadge');
    if(badge && j.code===0 && j.count>0){ badge.textContent=j.count; badge.style.display=''; }
    else if(badge){ badge.style.display='none'; }
  }catch(e){}
}
// 让工作台壳复用同一个刷新函数：否则 funds 页会同时跑两个 30s 轮询。
window.refreshFundsBadge = loadPending;

function loadStats(){
  BusinessUI.fetch('/funds/api/stats').then(function(response){return response.json()}).then(function(result){
    if(result.code!==0)throw new Error(result.msg||'统计加载失败');
    var d = result.data, cnt = d.record_count;
    document.getElementById('sIn').textContent = '¥'+Money.format(d.total_in);
    document.getElementById('sOut').textContent = '¥'+Money.format(d.total_out);
    var bal = d.balance;
    document.getElementById('sBal').textContent = '¥'+Money.format(bal);
    document.getElementById('sBal').style.color = Money.compare(bal,'0.00')>=0?'var(--funds-balance-positive, #1890ff)':'#ff4d4f';
    document.getElementById('sCnt').textContent = cnt;

    var h = '<table class="table table-sm small mb-0">';
    h += '<tr class="table-success"><td colspan="2"><b>进账来源 Top 5</b></td></tr>';
    d.source_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.source||'未指定')+'</td><td style="text-align:right">¥'+Money.format(s.total)+' ('+s.cnt+')</td></tr>'; });
    if(!d.source_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '<tr class="table-danger"><td colspan="2"><b>成员支出 Top 5</b></td></tr>';
    d.applicant_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.applicant||'未指定')+'</td><td style="text-align:right">¥'+Money.format(s.total)+' ('+s.cnt+')</td></tr>'; });
    if(!d.applicant_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '<tr><td colspan="2"><b>支出类别 Top 5</b></td></tr>';
    d.category_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.category||'其他')+'</td><td style="text-align:right">¥'+Money.format(s.total)+'</td></tr>'; });
    if(!d.category_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '</table>';
    document.getElementById('sumC').innerHTML = h;
  }).catch(function(e){ fundsToast('统计加载失败：'+e.message,'error'); });
}

function loadChart(type, btn){
  ct = type;
  chartDirty = false;
  chartPalette = document.documentElement.dataset.palette || '';
  var generation = ++chartRequest;
  chartAbort?.abort();
  chartAbort = new AbortController();
  document.querySelectorAll('#t-charts .btn-group .btn').forEach(function(b){ b.classList.remove('active'); });
  if(btn) btn.classList.add('active');
  document.getElementById('chartImg').hidden = true;
  document.getElementById('chartImg').style.display = 'none';
  document.getElementById('chartEmp').textContent = '正在生成图表…';
  document.getElementById('chartEmp').style.display = 'block';
  return BusinessUI.fetch('/funds/api/chart?'+new URLSearchParams({type:type,palette:chartPalette}), {signal:chartAbort.signal}).then(function(r){return r.json()}).then(function(j){
    if (generation !== chartRequest) return;
    if(j.code===0){
      document.getElementById('chartImg').src = j.data;
      document.getElementById('chartImg').hidden = false;
      document.getElementById('chartImg').style.display = 'inline';
      document.getElementById('chartEmp').style.display = 'none';
    } else {
      document.getElementById('chartEmp').textContent = j.msg || '图表加载失败，请重新点击图表类型重试';
      document.getElementById('chartEmp').style.display = 'block';
      document.getElementById('chartImg').hidden = true;
  document.getElementById('chartImg').style.display = 'none';
    }
  }).catch(function(e){
    if (generation !== chartRequest) return;
    document.getElementById('chartEmp').textContent='图表加载失败：'+e.message;
    document.getElementById('chartEmp').style.display='block';
    document.getElementById('chartImg').hidden=true;document.getElementById('chartImg').style.display='none';
  });
}

function doExport(){
  var u = '/funds/api/export?';
  var t = document.getElementById('ft').value, k = document.getElementById('fkw').value.trim();
  if(t) u += 'record_type='+t+'&';
  if(k) u += 'keyword='+encodeURIComponent(k);
  return BusinessUI.download(u, 'funds.csv');
}

// One in-flight operation per record; restore controls even when transport fails.
handleAdd = BusinessUI.guard(handleAdd, 'funds-add', '#addF button', text=>fundsToast(text,'error'));
saveE = BusinessUI.guard(saveE, 'funds-save', '#editMd button', text=>fundsToast(text,'error'));
delR = BusinessUI.guard(delR, id=>'funds-delete:'+id, id=>document.querySelectorAll(`#tbody button[onclick^="delR(${id},"]`), text=>fundsToast(text,'error'));
reviewRecord = BusinessUI.guard(reviewRecord, id=>'funds-review:'+id, id=>document.querySelectorAll(`#tbody button[onclick^="reviewRecord(${id},"]`), text=>fundsToast(text,'error'));
edR = BusinessUI.guard(edR, id=>'funds-edit:'+id, id=>document.querySelectorAll(`#tbody button[onclick="edR(${id})"]`), text=>fundsToast(text,'error'));
