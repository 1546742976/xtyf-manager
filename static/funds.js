
function escText(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function fundsToast(message, kind) {
  var box = document.getElementById('funds-feedback');
  if (!box) { alert(message); return; }
  box.textContent = message;
  box.className = 'ux-toast ' + (kind || '');
  clearTimeout(window.__fundsToastTimeout);
  window.__fundsToastTimeout = setTimeout(function() { box.hidden = true; }, 4200);
  box.hidden = false;
}
var API = '/funds/api/records', sort = {by:'record_date',order:'DESC'}, edM, ct=null;
var ME_NAME = window.XTYF_USER.name, ME_USERNAME = window.XTYF_USER.username;
var CAN_REVIEW = window.XTYF_USER.canReview;

document.addEventListener('DOMContentLoaded', function(){
  edM = new bootstrap.Modal('#editMd');
  document.getElementById('aDt').value = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  document.getElementById('aApplicant').value = ME_NAME;
  if (CAN_REVIEW) { loadPending(); setInterval(loadPending, 30000); }
  reload(); loadStats();
  document.getElementById('mainTab').addEventListener('shown.bs.tab', function(e){
    if (e.target.dataset.bsTarget === '#t-charts' && !ct) {
      loadChart('monthly', document.querySelector('#t-charts .btn-group .active'));
      loadStats();
    }
  });
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

function reload(){
  var p = new URLSearchParams();
  [{id:'ft',key:'record_type'},{id:'fkw',key:'keyword'},{id:'famin',key:'amount_min'},{id:'famax',key:'amount_max'},{id:'fdf',key:'date_from'},{id:'fdt',key:'date_to'}].forEach(function(x){
    var v = document.getElementById(x.id).value.trim(); if(v) p.append(x.key,v);
  });
  p.append('sort_by',sort.by); p.append('sort_order',sort.order);
  fetch(API+'?'+p).then(function(r){return r.json()}).then(function(j){
    if (!j || j.code !== 0) throw new Error((j && j.msg) || '服务端响应异常');
    var rows = j.data||[];
    document.getElementById('rcnt').textContent = rows.length + ' 条记录';
    document.getElementById('empty').style.display = rows.length?'none':'block';
    document.getElementById('tbody').innerHTML = rows.length ? rows.map(function(r){
      return '<tr>'+
        '<td>'+r.id+'</td>'+
        '<td><span class="'+(r.record_type==='进'?'badge-in':'badge-out')+'">'+(r.record_type==='进'?'进账':'支出')+'</span></td>'+
        '<td>¥'+r.amount.toFixed(2)+'</td>'+
        '<td>'+escText(r.category||'-')+'</td>'+
        '<td title="'+escText(r.source_dest||'')+'">'+escText(r.source_dest||'-')+'</td>'+
        '<td>'+escText(r.applicant||'-')+'</td>'+
        '<td>'+(r.invoice_count||0)+'</td>'+
        '<td>'+escText(r.handler||'-')+'</td>'+
        '<td>'+r.record_date+'</td>'+
        '<td>'+((r.review_status==='待审核')?'<span class="badge-pending">待审核</span> '+(CAN_REVIEW?'<button class="btn btn-outline-primary btn-sm" onclick="reviewRecord('+r.id+')">审核</button>':''):'<span class="badge-ok">已审核</span>')+'</td>'+
        '<td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="'+(r.notes||'').replace(/"/g,'&quot;')+'">'+escText(r.notes||'-')+'</td>'+
        '<td><button class="btn btn-outline-secondary btn-sm" onclick="edR('+r.id+')"><i class="bi bi-pencil"></i></button> <button class="btn btn-outline-danger btn-sm" onclick="delR('+r.id+')"><i class="bi bi-trash"></i></button></td>'+
        '</tr>';
    }).join('') : '';
  }).catch(function(e){ fundsToast('记录加载失败：'+e.message,'error'); });
}

async function handleAdd(e){
  e.preventDefault();
  var d = {
    record_type: document.getElementById('aT').value,
    amount: parseFloat(document.getElementById('aAmt').value),
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
    var r = await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    var j = await r.json();
    if(j.code===0){ fundsToast(j.review_status==='待审核' ? '记录已提交，等待财务审核' : '记录已添加','success'); clearF(); reload(); loadStats(); if(CAN_REVIEW)loadPending(); }
    else fundsToast(j.msg || '操作失败','error');
  } catch(e){ fundsToast('提交失败：'+e.message,'error'); }
}

async function edR(id){
  try {
    var r = await fetch(API+'?sort_by=id&sort_order=DESC'), j = await r.json();
    var d = j.data.find(function(x){return x.id===id});
    if(!d) return fundsToast('记录不存在','error');
    document.getElementById('eId').value=d.id; document.getElementById('eT').value=d.record_type;
    document.getElementById('eAmt').value=d.amount; document.getElementById('eCat').value=d.category||'';
    document.getElementById('eSD').value=d.source_dest||''; document.getElementById('eDt').value=d.record_date;
    document.getElementById('eInv').value=d.invoice_count||0; document.getElementById('eApl').value=d.applicant||'';
    document.getElementById('eHdl').value=d.handler||''; document.getElementById('eNts').value=d.notes||'';
    edM.show();
  } catch(e){ fundsToast('记录加载失败','error'); }
}

async function saveE(){
  var d = {
    record_type: document.getElementById('eT').value,
    amount: parseFloat(document.getElementById('eAmt').value),
    category: document.getElementById('eCat').value,
    source_dest: document.getElementById('eSD').value.trim(),
    record_date: document.getElementById('eDt').value,
    invoice_count: parseInt(document.getElementById('eInv').value)||0,
    applicant: document.getElementById('eApl').value.trim(),
    handler: document.getElementById('eHdl').value.trim(),
    notes: document.getElementById('eNts').value.trim()
  };
  try {
    var r = await fetch(API+'/'+document.getElementById('eId').value,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    var j = await r.json();
    if(j.code===0){ edM.hide(); fundsToast('记录已更新，审核状态可能需要重新确认','success'); reload(); loadStats(); }
    else fundsToast(j.msg || '操作失败','error');
  } catch(e){ fundsToast('保存失败','error'); }
}

async function delR(id){
  if(!confirm('确定永久删除此资金记录？此操作不可撤销。')) return;
  try {
    var response = await fetch(API+'/'+id,{method:'DELETE'});
    var result = await response.json();
    if (!response.ok || result.code !== 0) throw new Error(result.msg || '服务端拒绝删除');
    fundsToast('记录已删除','success');
    reload(); loadStats();
  } catch(e){ fundsToast('删除失败：'+e.message,'error'); }
}

async function reviewRecord(id){
  if(!CAN_REVIEW || !confirm('确认审核通过该记录？')) return;
  try{
    var r = await fetch(API+'/'+id+'/review',{method:'POST'});
    var j = await r.json();
    if(j.code===0){ fundsToast('已审核通过','success'); reload(); loadStats(); if(CAN_REVIEW)loadPending(); }
    else fundsToast(j.msg||'操作失败','error');
  }catch(e){ fundsToast('操作失败','error'); }
}

async function loadPending(){
  try{
    var r = await fetch('/funds/api/pending_review');
    var j = await r.json();
    var badge = document.getElementById('fundsPendingBadge');
    if(badge && j.code===0 && j.count>0){ badge.textContent=j.count; badge.style.display=''; }
    else if(badge){ badge.style.display='none'; }
  }catch(e){}
}

function loadStats(){
  Promise.all([fetch('/funds/api/stats'),fetch(API+'?sort_by=id&sort_order=DESC')]).then(function(arr){
    return Promise.all([arr[0].json(),arr[1].json()]);
  }).then(function(arr){
    var d = arr[0].data, cnt = (arr[1].data||[]).length;
    document.getElementById('sIn').textContent = '¥'+d.total_in.toFixed(2);
    document.getElementById('sOut').textContent = '¥'+d.total_out.toFixed(2);
    var bal = d.balance;
    document.getElementById('sBal').textContent = (bal>=0?'¥':'¥-')+Math.abs(bal).toFixed(2);
    document.getElementById('sBal').style.color = bal>=0?'#1890ff':'#ff4d4f';
    document.getElementById('sCnt').textContent = cnt;

    var h = '<table class="table table-sm small mb-0">';
    h += '<tr class="table-success"><td colspan="2"><b>进账来源 Top 5</b></td></tr>';
    d.source_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.source)+'</td><td style="text-align:right">¥'+s.total.toFixed(2)+' ('+s.cnt+')</td></tr>'; });
    if(!d.source_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '<tr class="table-danger"><td colspan="2"><b>成员支出 Top 5</b></td></tr>';
    d.applicant_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.applicant)+'</td><td style="text-align:right">¥'+s.total.toFixed(2)+' ('+s.cnt+')</td></tr>'; });
    if(!d.applicant_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '<tr><td colspan="2"><b>支出类别 Top 5</b></td></tr>';
    d.category_stats.slice(0,5).forEach(function(s){ h += '<tr><td>'+escText(s.category||'其他')+'</td><td style="text-align:right">¥'+s.total.toFixed(2)+'</td></tr>'; });
    if(!d.category_stats.length) h += '<tr><td colspan="2" class="text-muted">暂无数据</td></tr>';
    h += '</table>';
    document.getElementById('sumC').innerHTML = h;
  }).catch(function(e){ console.error(e); });
}

function loadChart(type, btn){
  ct = type;
  document.querySelectorAll('#t-charts .btn-group .btn').forEach(function(b){ b.classList.remove('active'); });
  if(btn) btn.classList.add('active');
  fetch('/funds/api/chart?type='+type).then(function(r){return r.json()}).then(function(j){
    if(j.code===0){
      document.getElementById('chartImg').src = j.data;
      document.getElementById('chartImg').style.display = 'inline';
      document.getElementById('chartEmp').style.display = 'none';
    } else {
      document.getElementById('chartEmp').innerHTML = '<p>暂无数据或图表生成失败</p>';
      document.getElementById('chartEmp').style.display = 'block';
      document.getElementById('chartImg').style.display = 'none';
    }
  }).catch(function(e){ console.error(e); });
}

function doExport(){
  var u = '/funds/api/export?';
  var t = document.getElementById('ft').value, k = document.getElementById('fkw').value.trim();
  if(t) u += 'record_type='+t+'&';
  if(k) u += 'keyword='+encodeURIComponent(k);
  window.location.href = u;
}
